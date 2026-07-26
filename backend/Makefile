# SAM custom build targets.
#
# SAM runs make with the working directory set to a *copy* of this folder, so
# every path here must stay inside it — anything reached via `../` is absent at
# build time. rubric.json therefore ships as a Lambda layer (see rubric/Makefile)
# rather than being copied in from the repo root.

build-ApiFunction: build-common
build-WorkerFunction: build-common

build-common:
	python -m pip install --quiet --disable-pip-version-check \
		--requirement requirements.txt --target "$(ARTIFACTS_DIR)"
	cp *.py "$(ARTIFACTS_DIR)/"
	cp -r ingestion agent api "$(ARTIFACTS_DIR)/"
	find "$(ARTIFACTS_DIR)" -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf "$(ARTIFACTS_DIR)/tests" "$(ARTIFACTS_DIR)/conftest.py"
	# boto3/botocore ship with the Lambda runtime — bundling them adds ~27 MB
	# per function for no benefit. They stay in requirements.txt for local dev.
	rm -rf "$(ARTIFACTS_DIR)"/boto3 "$(ARTIFACTS_DIR)"/botocore \
		"$(ARTIFACTS_DIR)"/boto3-*.dist-info "$(ARTIFACTS_DIR)"/botocore-*.dist-info
