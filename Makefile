all: depend
	pex -o ./build/kpsync -D src/ -r ./build/pex-requirements.txt -e kpsync:main

depend:
	mkdir -p ./build
	grep -vE '^(isort|black|mypy|pex|sphinx)' requirements.txt > ./build/pex-requirements.txt

typecheck:
	mypy src/

prepcommit:
	isort src/
	black src/

docs:
	mkdir -p docs/_static
	cd docs && sphinx-apidoc -f -o . ../src/ && $(MAKE) html

clean:
	$(RM) pykeepass_socket
	$(RM) -r ./build/ .mypy_cache ./src/__pycache__ ./src/*.pyc ./dist *.egg-info
	test -d docs && cd docs && $(MAKE) clean || true

requirements:
	pip freeze | grep -E "$(cat $(git root)/requirements.txt |cut -d= -f1|trimspaces|tr '\n' '|')''" >| $(git root)/requirements.txt


.PHONY: clean docs depend
