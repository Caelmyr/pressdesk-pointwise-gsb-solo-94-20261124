# PressDesk

PressDesk is a local workbench for inspecting two-ink raster separations and their simulated registration proof.

Run `./start.sh` and open [PressDesk](http://127.0.0.1:19897/). Use `./start.sh stop` to stop the container while preserving the project volume. `PRESSDESK_PORT` changes the loopback port. The same workbench can run as `python -B -m pressdesk` with the pinned dependencies on Python 3.11 or 3.12.

## Inspect a sheet

1. Open the synthetic Estuary sample or import two grayscale PNG separations. They must have the same dimensions, with black for full ink and white for paper.
2. Inspect either unmoved plate by itself. Set the cool plate X and Y offsets and preview the overprint. Positive values move the cool plate right and down, in half-pixel steps. The coverage view separates paper, one-ink and two-ink pixels at the selected cutoff. Shift changes highlights differences relative to zero offset, including newly exposed paper.
3. Mark a zone using two opposite image corners, then **Read cool offset** to estimate a half-pixel shift from the trial proof photo. Only cool ink gives evidence: the warm plate and paper stay fixed, and an axis whose edges give no signal is named instead of being guessed. A shift is offered for adoption only when the main contour and any fine adjacent line both stay within one pixel; edges still further out are drawn on the image. **Use this shift** keeps the half-pixel value, and **Read cool offset** again checks where the actual ink landed without rounding it back.
4. **Probe pixel** shows both coverage values and which original cool-plate pixels (with weights) produced the ink at that point; parts that moved in from beyond the plate count as paper white. Save the setup to retain the zones and simulation settings.
5. Add a prepared or independently uploaded proof photo with the same crop and pixel dimensions. Switch between the photograph and the RGB difference to the simulation. The separate photo preparation workspace can create a cropped or adjusted reference and send it to the currently open sheet.
6. Save and open a proof to recover the archived settings and source photo. Download the recomputed proof PNG, the coverage CSV or either original-coordinate separation with the visible links.

Each image supports up to 2.4 million pixels, 2400 pixels on the longest side and 12 MiB per upload. Plates are opaque grayscale PNGs. A proof photo can be an opaque PNG or JPEG and is normalized to its displayed orientation. The visual proof approximates ink transmission from coverage and the selected paper and ink colors. Coverage uses raster values, so the result makes no color-managed press prediction. The Estuary sheet is generated for practice.
