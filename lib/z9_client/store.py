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
Z9 mirror synchronization (design fix).

HP model — sync via `getMediumListVersion`:

  1. Z9 reachable → `getMediumListVersion` (small SOAP POST).
  2. Compare to `medium_list_version` stored in ``_mirror.json``.
  3. Identical → mirror up to date, nothing to do.
  4. Divergent → full ``getMediumList``, then for EACH paper:
     for EACH resident profile (slot GE × ColorSpace):
       - if the z9_uuid stored in the mirror == the remote one → skip
       - otherwise: targeted ``getProfile`` + write into the mirror.

**Pure mirror**: we download ALL resident profiles of ALL slots
(factory AND custom), bit-identical. The `custom` flag is metadata
stored in `_paper.json`, never an inclusion criterion. The sync NEVER
calls `donor-export`: that one stays a separate R&D command run on
user request.

Vocabulary (to respect in code and messages):
  - "donor paper" (firmware) = HP factory type from which a custom
    paper inherits its ink limits / inking curves at creation time.
    Permanent reference. Every custom paper has one.
  - "donor" (in the R&D sense) = factory ICC file extracted from a
    slot `custom="0"`, used as an R&D chart tag. May not exist
    (custom-only paper with no factory slot). This is NORMAL and the
    sync must not treat it as an error case.

Guardrail: `mirror/` never written outside this module +
``cache.save_donor``. `repo/` strictly intact.
"""

from __future__ import annotations

from typing import Any, Optional

from . import cache as _cache
from .exceptions import Z9Error


# ─── Schema versioning for what the sync writes ───────────────────────


# Root of the "frozen mirror". Increment MANUALLY on each change to the
# structure of what the sync writes (ICC file naming, ``_paper.json``
# format, ``_mirror.json`` format).
#
# History:
#   v0/v1 — 17.1 format (single donor, `donor` block at head of _paper.json)
#   v2    — 17.1.1+ format (slot-keyed, profiles[] without donor block)
#   v3    — 17.2.1 format (files named after faithful z9_icc_name,
#                          FS-sanitized only; collision disambiguated by slot)
#
# On the next ``store sync``: if the code has a schema > the one stored
# in the mirror, trigger a FULL PURGE of this serial's mirror + full
# refetch (even if the Z9 ``getMediumListVersion`` is identical).
SYNC_SCHEMA_VERSION = 3


# ─── Helpers ──────────────────────────────────────────────────────────


def _resolve_serial(client) -> str:
    """Return the Z9 serial number (in-memory cache at the client level
    if possible, otherwise an authenticated REST call).

    The serial is stable, so it can be memoized for the client's lifetime.
    """
    cached = getattr(client, "_freeglaz_serial_cache", None)
    if cached:
        return cached
    ident = client.identification()
    serial = (ident.get("SerialNumber")
              or ident.get("S/N")
              or ident.get("Serial")
              or ident.get("serial")
              or "")
    if not serial:
        raise Z9Error("Unable to read the Z9 serial number "
                      "(Identification.xml does not contain SerialNumber)")
    try:
        client._freeglaz_serial_cache = serial
    except AttributeError:
        pass
    return serial


def get_serial(client) -> str:
    """Public API: Z9 serial number (memoized)."""
    return _resolve_serial(client)


# ─── Status ───────────────────────────────────────────────────────────


def _count_profiles_in_paper_dir(paper_dir) -> tuple[int, int]:
    """Count (n_factory, n_custom) ICC profiles in a paper directory."""
    if not paper_dir.exists():
        return 0, 0
    manifest_path = paper_dir / "_paper.json"
    if not manifest_path.exists():
        return 0, 0
    try:
        import json
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:  # noqa: BLE001
        return 0, 0
    n_fac, n_cus = 0, 0
    for p in manifest.get("profiles") or []:
        if p.get("custom"):
            n_cus += 1
        else:
            n_fac += 1
    return n_fac, n_cus


def store_status(client=None) -> dict:
    """State of the local store. If ``client`` is provided and the Z9 is
    reachable, compare the local mirror version to the remote version.

    Without a client: purely local status (reading the manifests).

    Return format ::

        {
          "store_root": "/Users/.../freeglaz",
          "store_version": 1,
          "mirrors": [
            {
              "serial": "CNXXXXXXXX",
              "medium_list_version": "2026-05-26",
              "last_sync_at": "2026-05-29T18:00:00+02:00",
              "n_papers": 12,
              "n_donors": 7,
              "remote_version": "2026-05-26",   # if client provided
              "in_sync": true,                  # if client provided
            }, ...
          ],
          "backups": {"dir": "...", "n_files": N},
          "sessions": {"dir": "...", "n_dirs": N},
          "repo": {"printers": N, "displays": N, "workingspaces": N},
        }
    """
    _cache.ensure_store()
    root = _cache.root_dir()
    manifest = _cache.read_store_manifest()

    # Mirrors
    mirrors: list[dict[str, Any]] = []
    mdir = _cache.mirror_dir()
    if mdir.exists():
        for serial_dir in sorted(mdir.iterdir()):
            if not serial_dir.is_dir():
                continue
            serial = serial_dir.name
            mm = _cache.read_mirror_manifest(serial)
            papers_dir = _cache.mirror_papers_dir(serial)
            n_papers = 0
            n_profiles_factory = 0
            n_profiles_custom = 0
            if papers_dir.exists():
                for d in papers_dir.iterdir():
                    if not d.is_dir():
                        continue
                    n_papers += 1
                    nf, nc = _count_profiles_in_paper_dir(d)
                    n_profiles_factory += nf
                    n_profiles_custom += nc
            entry = {
                "serial": serial,
                "medium_list_version": mm.get("medium_list_version"),
                "last_sync_at": mm.get("last_sync_at"),
                "n_papers": n_papers,
                "n_profiles_factory": n_profiles_factory,
                "n_profiles_custom": n_profiles_custom,
                "n_profiles_total": n_profiles_factory + n_profiles_custom,
            }
            if client is not None:
                try:
                    remote = client.soap.get_medium_list_version()
                    entry["remote_version"] = remote.get("version")
                    entry["in_sync"] = (
                        mm.get("medium_list_version")
                        == remote.get("version")
                    )
                except Z9Error as e:
                    entry["remote_version"] = None
                    entry["in_sync"] = None
                    entry["remote_error"] = str(e)
            mirrors.append(entry)

    # Backups / sessions / repo
    def _count_files(p):
        return sum(1 for f in p.iterdir() if f.is_file()) if p.exists() else 0

    def _count_dirs(p):
        return sum(1 for d in p.iterdir() if d.is_dir()) if p.exists() else 0

    return {
        "store_root": str(root),
        "store_version": manifest.get("store_version", _cache.STORE_VERSION),
        "created_at": manifest.get("created_at"),
        "mirrors": mirrors,
        "backups": {"dir": str(_cache.backups_dir()),
                    # backups/<serial>/<mediaid>/<ge_state>/<ISO>.icc → recursive count
                    "n_files": (sum(1 for _ in _cache.backups_dir().rglob("*.icc"))
                                if _cache.backups_dir().exists() else 0)},
        "sessions": {"dir": str(_cache.sessions_dir()),
                     "n_dirs": _count_dirs(_cache.sessions_dir())},
        "repo": {
            "printers": _count_dirs(_cache.repo_printers_dir()),
            "displays": _count_files(_cache.repo_displays_dir()),
            "workingspaces": _count_files(_cache.repo_workingspaces_dir()),
        },
    }


# ─── Sync ─────────────────────────────────────────────────────────────


def _fetch_paper_slots(client, serial, p, cur_profiles, on_step=None):
    """Download into the mirror ALL resident slots of ONE paper.

    SHARED building block (event-driven sync factoring) between:
      - :func:`sync_mirror` (loop over touched papers);
      - :func:`refetch_paper` (forced targeted refetch after in-app mutation).

    The paper directory must have been purged BEFOREHAND by the caller
    (sync_mirror: per-paper purge; refetch_paper: targeted purge).

    :param p: rich paper dict (id, name, profiles[]) — list_full/details.
    :param cur_profiles: index ``{(ge, cs): {uuid, custom, icc_name, date}}``.
    :return: ``(profiles_fetched: list, errors: list, n_fetched: int)``.
    """
    def _step(label, **details):
        if on_step:
            try:
                on_step(label, **details)
            except Exception:  # noqa: BLE001
                pass

    paper_id = p.get("id")
    paper_name = p.get("name") or paper_id
    slug = _cache.slugify(paper_name)
    profiles_fetched: list = []
    errors: list = []

    # B1: collision detection on FS names (sanitized z9_icc_name).
    filenames_seen: set = set()
    slot_icc_names: dict = {}
    for slot_key, prof in cur_profiles.items():
        raw = prof.get("icc_name") or ""
        base = _cache.safe_fs_name(raw) if raw else ""
        if not base or base in filenames_seen:
            ge_, cs_ = slot_key
            suffix = f"GE-{ge_}__{cs_}"
            disambig = f"{base}_{suffix}" if base else suffix
            slot_icc_names[slot_key] = disambig
            filenames_seen.add(disambig)
        else:
            slot_icc_names[slot_key] = base
            filenames_seen.add(base)

    n_fetched = 0
    for slot_key, prof in cur_profiles.items():
        ge, cs_soap = slot_key
        dest_path = _cache.mirror_profile_path(
            serial, paper_name, ge, cs_soap,
            custom=prof["custom"],
            icc_name=slot_icc_names.get(slot_key),
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        if cs_soap == "PRINTER_RGB":
            cs_user = "RGB"
        elif cs_soap == "PRINTER_GRAYSCALE":
            cs_user = "GRAYSCALE"
        else:
            errors.append({
                "paper_id": paper_id, "paper_name": paper_name,
                "slot": f"{ge}/{cs_soap}",
                "stage": "translate_color_space",
                "error": f"ColorSpace inconnu : {cs_soap}",
            })
            continue

        try:
            _step("fetch_profile",
                  slug=slug, paper_name=paper_name,
                  ge=ge, color_space=cs_soap, custom=prof["custom"])
            result = client.paper.export_icc(
                ref=paper_id,
                output_path=str(dest_path),
                gloss_enhancer=ge,
                color_space=cs_user,
                _pre_resolved=p,
            )
            _cache.update_paper_manifest_for_profile(
                serial=serial,
                paper=p,
                profile={
                    "gloss_enhancer": ge,
                    "color_space": cs_soap,
                    "custom": prof["custom"],
                    "uuid": prof.get("uuid"),
                    "icc_name": prof.get("icc_name"),
                    "date": prof.get("date"),
                },
                filename=dest_path.name,
                md5=result.get("md5"),
                size_bytes=result.get("size_bytes", 0),
            )
            profiles_fetched.append({
                "paper_name": paper_name, "slug": slug,
                "ge": ge, "color_space": cs_soap,
                "custom": prof["custom"],
            })
            n_fetched += 1
        except Z9Error as e:
            errors.append({
                "paper_id": paper_id, "paper_name": paper_name,
                "slot": f"{ge}/{cs_soap}",
                "stage": "export_icc",
                "error": str(e),
            })
    return profiles_fetched, errors, n_fetched


def refetch_paper(client, ref, on_step=None) -> dict:
    """FORCED refetch of ONE single paper in the mirror (bypasses the version gate).

    To be called AFTER an in-app mutation of a slot/profile (``set_profile`` /
    import-icc, ``new_profile`` / scan / paper profile, ``calibrate`` CLC,
    ``delete_profile``). Since the ``getMediumListVersion`` gate is BLIND to
    these changes (they don't bump the version), we directly refresh the cache
    of the touched paper: targeted purge then re-download of its slots.

    Best-effort: the caller must wrap the call so that a refetch failure
    (Z9 momentarily unreachable) does NOT fail the already-successful
    mutation — just a mirror left to be refreshed.

    :param ref: MediumId or name of the mutated paper.
    :return: dict ``{serial, slug, paper_name, n_profiles_fetched, errors}``.
    """
    def _step(label, **details):
        if on_step:
            try:
                on_step(label, **details)
            except Exception:  # noqa: BLE001
                pass

    _cache.ensure_store()
    serial = _resolve_serial(client)
    _cache.ensure_mirror(serial)

    p = client.paper.details(ref)
    if not p or not p.get("id"):
        raise Z9Error(f"refetch_paper: paper not found ({ref!r})")
    paper_id = p["id"]
    paper_name = p.get("name") or paper_id
    slug = _cache.slugify(paper_name)

    cur_profiles: dict = {}
    for prof in p.get("profiles") or []:
        ge = prof.get("gloss_enhancer") or "UNKNOWN"
        cs = prof.get("color_space") or "UNKNOWN"
        cur_profiles[(ge, cs)] = {
            "uuid": prof.get("uuid"),
            "custom": bool(prof.get("custom")),
            "icc_name": prof.get("icc_name"),
            "date": prof.get("date"),
        }
    cur_uuids = {
        f"{ge}/{cs}": pr.get("uuid")
        for (ge, cs), pr in cur_profiles.items()
    }

    _step("refetch_paper_start", slug=slug, paper_name=paper_name)
    # Targeted purge of the paper then FORCED refetch (we NEVER go through
    # the getMediumListVersion gate, blind to profile changes).
    _cache.purge_mirror_paper(serial, paper_name)
    profiles_fetched, errors, n_fetched = _fetch_paper_slots(
        client, serial, p, cur_profiles, on_step=on_step)

    # Update the _mirror.json index for this paper (UUIDs per slot).
    manifest = _cache.read_mirror_manifest(serial)
    papers = dict(manifest.get("papers") or {})
    papers[slug] = {
        "paper_id": paper_id,
        "paper_name": paper_name,
        "is_user_custom": bool(p.get("is_user_custom")),
        "donor_paper_id": p.get("donor_id"),
        "profiles_uuids": cur_uuids,
        "n_profiles": len(cur_profiles),
    }
    manifest["papers"] = papers
    manifest["last_sync_at"] = _cache._now_iso()
    _cache.write_mirror_manifest(serial, manifest)

    _step("refetch_paper_done", slug=slug,
          n_profiles_fetched=n_fetched, errors=len(errors))
    return {
        "serial": serial, "slug": slug, "paper_name": paper_name,
        "n_profiles_fetched": n_fetched, "errors": errors,
    }


def forget_paper(client, paper_name, on_step=None) -> dict:
    """Remove ONE paper from the mirror (after the medium is deleted on the Z9).

    Event-driven sync for ``delete`` (medium removed): we purge the paper's
    mirror directory + remove its entry from ``_mirror.json``. Best-effort
    on the caller side.

    :return: ``{serial, slug, purged, removed_from_index}``.
    """
    _cache.ensure_store()
    serial = _resolve_serial(client)
    _cache.ensure_mirror(serial)
    slug = _cache.slugify(paper_name)
    purged = _cache.purge_mirror_paper(serial, paper_name)
    manifest = _cache.read_mirror_manifest(serial)
    papers = dict(manifest.get("papers") or {})
    removed = papers.pop(slug, None) is not None
    manifest["papers"] = papers
    manifest["last_sync_at"] = _cache._now_iso()
    _cache.write_mirror_manifest(serial, manifest)
    if on_step:
        try:
            on_step("forget_paper", slug=slug, purged=bool(purged))
        except Exception:  # noqa: BLE001
            pass
    return {"serial": serial, "slug": slug,
            "purged": bool(purged), "removed_from_index": removed}


def sync_mirror(client, on_step=None, force: bool = False) -> dict:
    """Synchronize the local mirror with the Z9 (pure mirror).

    For EACH paper on the Z9, download ALL resident profiles of ALL slots
    (factory AND custom), bit-identical. No paper is excluded for lack of
    a donor: a custom-only paper with custom profiles belongs in the
    mirror just as much as a factory paper.

    1. Fetch the Z9 serial + remote ``medium_list_version``.
    2. If identical to the local version and ``force=False`` → return
       ``changed=False`` immediately, without fetching.
    3. Otherwise: full ``getMediumList`` (paper.list_full → rich SOAP
       with all profiles), then for each (paper, profile_slot):
         - read the UUID stored in the mirror (``_paper.json``);
         - if identical to the remote UUID → skip (network saving);
         - otherwise: ``soap.get_profile(medium_id, ge, cs)`` + write the
           slot-keyed ICC file into the mirror + update
           ``_paper.json``.
    4. Update ``_mirror.json`` (version + date).

    :param client: connected Z9Client instance.
    :param on_step: optional ``(stage_name, **details)`` callback.
    :param force: ignore the local version and resync everything.
    :return: summary dict (see keys below).
    """
    def _step(label, **details):
        if on_step:
            try:
                on_step(label, **details)
            except Exception:  # noqa: BLE001
                pass

    _cache.ensure_store()
    serial = _resolve_serial(client)
    _cache.ensure_mirror(serial)
    manifest = _cache.read_mirror_manifest(serial)

    _step("version_check", serial=serial)
    remote = client.soap.get_medium_list_version()
    remote_version = remote.get("version")
    local_version = manifest.get("medium_list_version")
    local_schema = int(manifest.get("sync_schema_version") or 0)
    schema_outdated = local_schema < SYNC_SCHEMA_VERSION
    version_match = (remote_version == local_version) and bool(local_version)

    _step("version_known",
          local=local_version, remote=remote_version, force=force,
          local_schema=local_schema, code_schema=SYNC_SCHEMA_VERSION,
          schema_outdated=schema_outdated)

    # No-op only if: no force + identical Z9 version + up-to-date schema
    if not force and version_match and not schema_outdated:
        manifest["last_check_at"] = _cache._now_iso()
        _cache.write_mirror_manifest(serial, manifest)
        return {
            "serial": serial,
            "changed": False,
            "local_version": local_version,
            "remote_version": remote_version,
            "local_schema": local_schema,
            "code_schema": SYNC_SCHEMA_VERSION,
            "n_papers_total": len(manifest.get("papers") or {}),
            "n_papers_updated": 0,
            "n_profiles_fetched": 0,
            "profiles_fetched": [],
            "errors": [],
            "purge": None,
        }

    # Purge decision (Fix A):
    #   - code schema > stored schema → FULL PURGE of serial (the format
    #     changed, the local content is stale even if the Z9 table is
    #     not).
    #   - force=True             → FULL PURGE (explicit intent).
    #   - otherwise (Z9 divergence) → PER-PAPER purge, just before
    #     rewriting its slots (cf. loop below, Fix B).
    full_purge = force or schema_outdated
    purge_info = None
    if full_purge:
        reason = "schema_upgrade" if schema_outdated else "force"
        _step("full_purge_start", serial=serial, reason=reason,
              local_schema=local_schema, code_schema=SYNC_SCHEMA_VERSION)
        n_purged = _cache.purge_mirror_serial_papers(serial)
        _step("full_purge_done", n_papers_purged=n_purged, reason=reason)
        # Reset the _mirror.json paper index — it will be rebuilt below
        manifest["papers"] = {}
        purge_info = {"mode": "full", "reason": reason,
                      "n_papers_purged": n_purged}

    # Divergence (or force / schema): full rich list (papers + profiles).
    _step("fetch_paper_list", serial=serial)
    papers = client.paper.list_full()
    _step("paper_list_received", n=len(papers))

    prev_papers = dict(manifest.get("papers") or {})
    new_papers: dict[str, Any] = {}
    updated_slugs: list[str] = []
    papers_purged: list[str] = []
    profiles_fetched: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for p in papers:
        paper_id = p.get("id")
        paper_name = p.get("name") or paper_id
        if not paper_id or not paper_name:
            continue
        slug = _cache.slugify(paper_name)

        # Index { (ge, cs) : profile } for the diff vs the local mirror.
        cur_profiles: dict[tuple, dict[str, Any]] = {}
        for prof in p.get("profiles") or []:
            ge = prof.get("gloss_enhancer") or "UNKNOWN"
            cs = prof.get("color_space") or "UNKNOWN"
            cur_profiles[(ge, cs)] = {
                "uuid": prof.get("uuid"),
                "custom": bool(prof.get("custom")),
                "icc_name": prof.get("icc_name"),
                "date": prof.get("date"),
            }

        # Fix B: "touched paper" decision at PAPER GRAIN. We compare the
        # current set of (slot → uuid) vs the version stored in
        # _mirror.json. If different AND not in full_purge, we PURGE this
        # paper's directory before refetch, then refetch ALL current
        # slots — no per-slot incremental (guarantees zero orphans).
        cur_uuids = {
            f"{ge}/{cs}": prof.get("uuid")
            for (ge, cs), prof in cur_profiles.items()
        }
        prev_uuids = (prev_papers.get(slug) or {}).get("profiles_uuids") or {}
        paper_changed = (cur_uuids != prev_uuids) or (slug not in prev_papers)

        if not paper_changed and not full_purge:
            # Unchanged paper: keep the _mirror.json entry as is.
            # The local _paper.json and the ICC files are not touched.
            new_papers[slug] = prev_papers[slug]
            continue

        # Touched paper (or full_purge): purge the directory then refetch
        # all slots. In full_purge the whole serial was already purged
        # above → purge_mirror_paper is a no-op (directory absent).
        if not full_purge:
            purged = _cache.purge_mirror_paper(serial, paper_name)
            if purged:
                papers_purged.append(slug)
                _step("paper_purge", slug=slug, paper_name=paper_name)

        # Download this paper's slots — SHARED building block with
        # refetch_paper (event-driven sync factoring).
        pf, errs, n_fetched_for_paper = _fetch_paper_slots(
            client, serial, p, cur_profiles, on_step=on_step)
        profiles_fetched.extend(pf)
        errors.extend(errs)

        # Paper index in _mirror.json: we store only the per-slot UUIDs
        # (for the version check). The detail lives in the local
        # _paper.json.
        new_papers[slug] = {
            "paper_id": paper_id,
            "paper_name": paper_name,
            "is_user_custom": bool(p.get("is_user_custom")),
            "donor_paper_id": p.get("donor_id"),
            "profiles_uuids": cur_uuids,
            "n_profiles": len(cur_profiles),
        }
        if n_fetched_for_paper > 0:
            updated_slugs.append(slug)

    manifest.update({
        "serial": serial,
        "medium_list_version": remote_version,
        "sync_schema_version": SYNC_SCHEMA_VERSION,
        "last_sync_at": _cache._now_iso(),
        "last_check_at": _cache._now_iso(),
        "papers": new_papers,
        "errors": errors,
    })
    _cache.write_mirror_manifest(serial, manifest)

    if not full_purge and papers_purged:
        purge_info = {"mode": "per_paper",
                      "reason": "uuid_change",
                      "n_papers_purged": len(papers_purged),
                      "papers": papers_purged}

    return {
        "serial": serial,
        "changed": True,
        "local_version": local_version,
        "remote_version": remote_version,
        "local_schema": local_schema,
        "code_schema": SYNC_SCHEMA_VERSION,
        "n_papers_total": len(new_papers),
        "n_papers_updated": len(updated_slugs),
        "n_profiles_fetched": len(profiles_fetched),
        "profiles_fetched": profiles_fetched,
        "purge": purge_info,
        "errors": errors,
    }
