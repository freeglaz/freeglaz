# freeglaz icon

The freeglaz brand mark — a glaz-gradient rounded square with the inverted white
"Z", the same geometry as the TopNav `ZMonogram` and the splash.

- `freeglaz.svg` — vector source. The stop colors are the sRGB equivalents of the
  app's oklch gradient (SVG rasterizers do not render `oklch()`): `#0A5063` →
  `#2E8688` → `#75B3A8`.
- `freeglaz-1024.png` — 1024×1024 raster.
- `freeglaz.icns` — multi-resolution macOS icon (16→1024, `@2x`).

Uses: the macOS `.app` launcher icon (see `Docs/install_bonus_macos.md`) and the
optional desktop-window Dock icon.

Regenerate:

```bash
rsvg-convert -w 1024 -h 1024 freeglaz.svg -o freeglaz-1024.png
mkdir freeglaz.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s freeglaz-1024.png --out "freeglaz.iconset/icon_${s}x${s}.png"
  sips -z $((s*2)) $((s*2)) freeglaz-1024.png --out "freeglaz.iconset/icon_${s}x${s}@2x.png"
done
iconutil -c icns freeglaz.iconset -o freeglaz.icns
```
