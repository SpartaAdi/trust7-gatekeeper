# Deployment-verification fixtures

Inputs for `scripts/verify_docker_service.sh`. Both are **synthetic and invented**
for that script. Neither describes a real client, engagement, system or person, and
neither contains personal, client or commercial information — they get uploaded to a
deployed service, so that is a requirement, not a preference.

| File | What it is |
| --- | --- |
| `order-intake-sow.md` | A fictional SoW for an order-intake platform. |
| `order-intake-architecture.png` | A 9-box architecture diagram of the same system, drawn as a raster image. |

Two properties are deliberate and worth preserving if either is ever regenerated.

**The SoW is mixed on purpose.** Some controls are genuinely present and some are
genuinely absent — the absent ones are listed under "Known gaps, stated for the
reviewer". A review of it therefore produces a spread of pass, partial and fail. A
document that scored uniformly would not tell you the pipeline was evaluating, only
that it was answering.

**The diagram is a PNG, not a `.drawio`.** The OCR coverage proxy is only computed
on the image path. A `.drawio` upload returns `ocr_proxy: null` — correct, and
indistinguishable at a glance from Tesseract being absent — so it would verify the
wrong thing on the one service whose reason to exist is that Tesseract is installed
in it. The labels are plain horizontal text on a light background because the point
is to give OCR something it can read; a stylised diagram would produce a low proxy
figure that says nothing about the deployment.

Structural coverage is not exercised by these two files. It is a `.drawio`-only
metric, and the script reports it as `not applicable`. `fixtures/` has `.drawio`
designs if you want to check that path instead — you will then get
`ocr_proxy: null`, and that is expected.
