"""Chart print placement nudge (diagnostic for the Z9 Pro left-clip).

PrintChartBody exposes optional offset_x_mm/offset_y_mm (default 0.0, so the
normal centered placement is unchanged) that thread into PrintParams — letting a
user empirically correct a model-specific ROLL margin without a rebuild. Full
threading to the geometry needs a live Z9; here we lock the request contract and
that PrintParams carries the same fields.
"""
from webapp.backend.models import PrintParams
from webapp.backend.routes.charts import PrintChartBody


def test_print_chart_body_defaults_are_zero():
    b = PrintChartBody()
    assert b.offset_x_mm == 0.0 and b.offset_y_mm == 0.0


def test_print_chart_body_accepts_offsets():
    b = PrintChartBody(quality="HIGH", offset_x_mm=15.0, offset_y_mm=3.0)
    assert b.offset_x_mm == 15.0 and b.offset_y_mm == 3.0


def test_offsets_map_onto_printparams():
    b = PrintChartBody(offset_x_mm=15.0, offset_y_mm=3.0)
    p = PrintParams(gloss_enhancer="OFF", quality=b.quality, rendermode="COLOR",
                    offset_x_mm=b.offset_x_mm, offset_y_mm=b.offset_y_mm)
    assert p.offset_x_mm == 15.0 and p.offset_y_mm == 3.0
