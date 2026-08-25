# Real client documents — not committed

`tests/test_embedded_diagram.py::test_the_real_rmbl_sow_selects_the_page_8_diagram`
looks here for:

    RMBL-Control-Tower-SOW-v1.0 (Signed).pdf

It is a signed client contract, so it is deliberately NOT in the repository. The
test skips with a message naming this path when the file is absent, and asserts
the selected image's page, dimensions and SHA-256 when it is present.

Drop the file in and run:

    python3 -m pytest tests/test_embedded_diagram.py -q -rs

Everything in this directory except this README is git-ignored.
