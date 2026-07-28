# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Parsers to transform the Z9's XML/JSON responses into Python objects.

We use xml.etree.ElementTree from the stdlib (no external lxml
dependency, to stay lightweight). All the PIWS XML have piws/al/jq/etc.
namespaces, which we handle via {namespace}tag in the lookups.
"""

import xml.etree.ElementTree as ET
import json


# Main PIWS namespace
NS_PIWS = "{http://www.hp.com/schemas/piws/v2_0}"


def _localname(tag):
    """Strip the namespace from a tag: '{ns}foo' -> 'foo'."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _element_to_dict(elem):
    """
    Recursive conversion of an XML Element into a Python dict.

    Rules:
      - If the element has children -> dict of the children
      - If several children have the same tag -> list
      - If a leaf with text -> str (or None if empty)
      - All tags are localized (without namespace prefix)
    """
    children = list(elem)
    if not children:
        text = (elem.text or "").strip()
        return text if text else None

    result = {}
    for child in children:
        key = _localname(child.tag)
        value = _element_to_dict(child)
        if key in result:
            # Several children with the same tag -> list
            if not isinstance(result[key], list):
                result[key] = [result[key]]
            result[key].append(value)
        else:
            result[key] = value
    return result


def parse_xml(xml_text):
    """
    Parse a PIWS XML and return a dict.

    The dict is flat from the root, without a useless wrapper.
    """
    root = ET.fromstring(xml_text)
    return {_localname(root.tag): _element_to_dict(root)}


def parse_paper_list(xml_text):
    """
    Parse /Paper/List or /LFPWebServices/PI/PaperListStatus.xml.

    Return a list of Paper dicts with:
      - id (str)              : 32-char hex MediumId or numeric factory ID
      - category_id (str)     : CUSTOM, PHOTO, FINE_ART, BOND_AND_COATED, etc.
      - donor_id (str|None)   : if custom, the numeric ID of the HP donor paper
      - revision (str)        : paper driver version (e.g. "4.1.3")
      - name (str)            : en_US localized name
      - is_visible (bool)
      - is_factory (bool)     : True if HP factory paper, False if user custom
      - is_protected (bool)
      - properties (dict)     : map of all the Properties (cutter-enabled, etc.)
    """
    root = ET.fromstring(xml_text)
    # We accept two structures: <piws:PaperList><List><Paper>...
    # or <piws:PaperListStatus><List><Paper>...
    list_node = root.find(f"{NS_PIWS}List")
    if list_node is None:
        list_node = root.find("List")  # sometimes without NS
    if list_node is None:
        return []

    papers = []
    for paper_elem in list_node.findall(f"{NS_PIWS}Paper") + list_node.findall("Paper"):
        paper = _parse_paper_element(paper_elem)
        papers.append(paper)
    return papers


def _parse_paper_element(elem):
    """Parse a <Paper>...</Paper> into a structured dict."""
    def find_text(tag, default=None):
        node = elem.find(f"{NS_PIWS}{tag}")
        if node is None:
            node = elem.find(tag)
        if node is None:
            return default
        return (node.text or "").strip() or default

    def find_bool(tag, default=False):
        v = find_text(tag)
        return v == "true" if v else default

    paper = {
        "id": find_text("Id", ""),
        "category_id": find_text("CategoryId", ""),
        "donor_id": find_text("DonorId"),
        "revision": find_text("Revision", ""),
        "is_visible": find_bool("IsVisible"),
        "is_factory": find_bool("IsFactory"),
        "is_protected": find_bool("IsProtected"),
        "name": None,
        "properties": {},
    }

    # is_user_custom: really created by the user?
    # Heuristic: the ID is a 32-character hexadecimal UUID
    # (HP factory papers have short numeric IDs like "1100", "4060")
    pid = paper["id"]
    paper["is_user_custom"] = (
        len(pid) == 32
        and all(c in "0123456789ABCDEFabcdef" for c in pid)
    )

    # Localizations: take en_US by default
    loc_node = elem.find(f"{NS_PIWS}Localizations")
    if loc_node is None:
        loc_node = elem.find("Localizations")
    if loc_node is not None:
        for loc in list(loc_node):
            iso = loc.find(f"{NS_PIWS}ISOCode")
            if iso is None:
                iso = loc.find("ISOCode")
            val = loc.find(f"{NS_PIWS}Value")
            if val is None:
                val = loc.find("Value")
            if iso is not None and val is not None and iso.text == "en_US":
                paper["name"] = (val.text or "").strip()
                break

    # Properties: map name -> value
    props_node = elem.find(f"{NS_PIWS}Properties")
    if props_node is None:
        props_node = elem.find("Properties")
    if props_node is not None:
        for prop in list(props_node):
            name_node = prop.find(f"{NS_PIWS}Name")
            if name_node is None:
                name_node = prop.find("Name")
            versatype = prop.find(f"{NS_PIWS}Versatype")
            if versatype is None:
                versatype = prop.find("Versatype")
            if name_node is None or versatype is None:
                continue
            value_node = versatype.find(f"{NS_PIWS}Value")
            if value_node is None:
                value_node = versatype.find("Value")
            if value_node is None:
                continue
            name = (name_node.text or "").strip()
            value = (value_node.text or "").strip()
            paper["properties"][name] = value

    return paper


def parse_json(text):
    """Parse a JSON response (Calibrations.json, InkSystem.json, etc.)."""
    return json.loads(text)


# ─── SOAP getMediumList parser (verbose, full of info) ──────────────

# EngineOperations namespace used in the SOAP getMediumList responses
NS_EO = "{http://www.bpo.hp.com/EngineOperations}"
NS_PD = "{http://www.bpo.hp.com/PaperDetails}"


def _eo_text(elem, tag, default=None):
    """Get the text of an EngineOperations:tag sub-element."""
    node = elem.find(f"{NS_EO}{tag}")
    if node is None:
        return default
    return (node.text or "").strip() or default


def _eo_bool(elem, tag, default=False):
    """Read a sub-element that contains '0' or '1'."""
    v = _eo_text(elem, tag)
    if v is None:
        return default
    return v == "1"


def parse_soap_medium_list(xml_text):
    """
    Parse a SOAP <GET-MEDIUM-LIST-RESPONSE> response into a list of papers
    enriched with ALL the internal fields (vs the REST which gives fewer).

    Return a list of dicts with:
      - Base fields: id, name, short_name, category_id, billing_type_id,
                     donor_id, revision, ws_revision, is_factory,
                     is_visible, is_protected, is_locked, is_user_custom
      - calibration: {date, obsolete}
      - profiles: list of {custom, date, icc_name, uuid, gloss_enhancer, color_space}
      - capabilities: dict of Properties/Fixed (booleans / values)
      - settings: dict of Properties/Variable (physical settings)
      - details: dict of PaperDetails (grammage, inks, etc.)
      - printmodes_halftone: list (often empty)
      - printmodes_contone: list

    :param xml_text: raw SOAP response
    :return: list of dicts
    """
    root = ET.fromstring(xml_text)
    # Navigate Body > GET-MEDIUM-LIST-RESPONSE > MediumList > Medium*
    body = None
    for child in root:
        if _localname(child.tag) == "Body":
            body = child
            break
    if body is None:
        return []

    response = None
    for child in body:
        if _localname(child.tag) == "GET-MEDIUM-LIST-RESPONSE":
            response = child
            break
    if response is None:
        return []

    medium_list = None
    for child in response:
        if _localname(child.tag) == "MediumList":
            medium_list = child
            break
    if medium_list is None:
        return []

    papers = []
    for child in medium_list:
        if _localname(child.tag) == "Medium":
            papers.append(_parse_soap_medium(child))
    return papers


def _parse_soap_medium(elem):
    """Parse an <EngineOperations:Medium> into a rich dict."""
    paper = {
        # Identifiers
        "id": _eo_text(elem, "MediumId", ""),
        "version_internal": _eo_text(elem, "Version", ""),
        "checksum": _eo_text(elem, "MediaChecksum", ""),
        "revision": _eo_text(elem, "Revision", ""),
        "ws_revision": _eo_text(elem, "wsRevision", ""),

        # Categorization
        "category_id": _eo_text(elem, "CategoryId", ""),
        "category_name": None,
        "billing_type_id": _eo_text(elem, "BillingTypeId", ""),
        "is_factory": _eo_bool(elem, "factory"),
        "is_visible": _eo_bool(elem, "Visible"),
        "is_protected": _eo_bool(elem, "ProtectedPaper"),
        "is_locked": _eo_bool(elem, "Locked"),

        # en_US localized name
        "name": None,
        "short_name": None,

        # Donor
        "donor_id": _eo_text(elem, "DonorId"),

        # Calibration (the only true CLC state easily accessible here)
        "calibration": None,

        # Associated ICC profiles
        "profiles": [],

        # Fixed capabilities (immutable booleans / values)
        "capabilities": {},
        # Variable settings (adjustable: starwheel, drying, etc.)
        "settings": {},
        # Paper details (grammage, inks used)
        "details": {},
        # PrintModes
        "printmodes_halftone": [],
        "printmodes_contone": [],
    }

    # is_user_custom (32-char hex UUID)
    pid = paper["id"]
    paper["is_user_custom"] = (
        len(pid) == 32
        and all(c in "0123456789ABCDEFabcdef" for c in pid)
    )

    # en_US Localization
    loc = elem.find(f"{NS_EO}Localization")
    if loc is not None:
        paper["name"] = _eo_text(loc, "Name")
        paper["short_name"] = _eo_text(loc, "ShortName")

    # CategoryLocalization in an attribute
    cat_loc = elem.find(f"{NS_EO}CategoryLocalization")
    if cat_loc is not None:
        paper["category_name"] = cat_loc.get("Name")

    # Calibration in attributes
    cal = elem.find(f"{NS_EO}Calibration")
    if cal is not None:
        paper["calibration"] = {
            "date": cal.get("date"),
            "obsolete": cal.get("obsolete") == "1",
        }

    # ProfilingTickets (there can be several)
    for pt in elem.findall(f"{NS_EO}ProfilingTicket"):
        raw_date = pt.get("date")
        # Date normalization:
        #   - Factory: "2018-03-20"                  -> "2018-03-20"
        #   - Custom : "[2026-05-13 11:54:10.072]"   -> "2026-05-13"
        # We extract just the date part in YYYY-MM-DD format
        normalized_date = raw_date
        if raw_date:
            import re as _re
            match = _re.search(r"(\d{4}-\d{2}-\d{2})", raw_date)
            if match:
                normalized_date = match.group(1)
        profile = {
            "custom": pt.get("custom") == "1",
            "date": normalized_date,        # normalized YYYY-MM-DD
            "date_raw": raw_date,           # raw firmware value
            "icc_name": pt.get("iccName"),
            "uuid": pt.get("uuid"),
            "gloss_enhancer": None,
            "color_space": None,
        }
        key = pt.find(f"{NS_EO}Key")
        if key is not None:
            profile["gloss_enhancer"] = _eo_text(key, "GlossEnhancer")
            profile["color_space"] = _eo_text(key, "ColorSpace")
        paper["profiles"].append(profile)

    # Properties (Fixed + Variable)
    props = elem.find(f"{NS_EO}Properties")
    if props is not None:
        fixed = props.find(f"{NS_EO}Fixed")
        if fixed is not None:
            # Attributes of Fixed
            for attr_name, attr_val in fixed.attrib.items():
                paper["capabilities"][_localname(attr_name)] = attr_val
            # Sub-elements of Fixed
            for sub in fixed:
                tag = _localname(sub.tag)
                paper["capabilities"][tag] = (sub.text or "").strip()

        variable = props.find(f"{NS_EO}Variable")
        if variable is not None:
            for attr_name, attr_val in variable.attrib.items():
                paper["settings"][_localname(attr_name)] = attr_val
            for sub in variable:
                tag = _localname(sub.tag)
                paper["settings"][tag] = (sub.text or "").strip()

    # PaperDetails
    details = elem.find(f"{NS_EO}Details")
    if details is not None:
        for d in details:
            # d is a PaperDetails:detail with attributes desc, id, strValue or boolValue
            desc = d.get("desc", "")
            if d.get("strValue") is not None:
                paper["details"][desc] = d.get("strValue")
            elif d.get("boolValue") is not None:
                paper["details"][desc] = d.get("boolValue") == "1"

    # PrintModes (may be empty)
    halftone = elem.find(f"{NS_EO}supportedPrintmodesHalftonePath")
    if halftone is not None:
        for pm in halftone:
            paper["printmodes_halftone"].append(_localname(pm.tag))
    contone = elem.find(f"{NS_EO}supportedPrintmodesContonePath")
    if contone is not None:
        for pm in contone:
            paper["printmodes_contone"].append(_localname(pm.tag))

    return paper
