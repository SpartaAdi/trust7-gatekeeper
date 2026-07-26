# Packages rubric.json as a Lambda layer, shared by both functions.
#
# SAM runs this with the working directory set to a *copy* of this folder, so the
# path must be relative to here — a `../` reference out of the CodeUri does not
# resolve at build time.

build-RubricLayer:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp rubric.json "$(ARTIFACTS_DIR)/rubric.json"
