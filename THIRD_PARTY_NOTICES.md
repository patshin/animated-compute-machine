# Third-party notices

AGENT//TRACE uses the third-party packages listed below. Each package remains under its own license; the repository's Apache-2.0 license does not replace those terms. Package names link to their authoritative package index records, where upstream copyright notices and license texts are available.

This production inventory was generated from `pnpm-lock.yaml`, `requirements.production.lock`, the exact build backend in `pyproject.toml`, and installed distribution metadata for the v0.1.0-rc.2 candidate. Build-only packages are not shipped in the runtime containers unless listed below.

## Browser production dependency graph

| Package | Version | License |
|---|---:|---|
| [react](https://www.npmjs.com/package/react) | 19.2.8 | MIT |
| [react-dom](https://www.npmjs.com/package/react-dom) | 19.2.8 | MIT |
| [scheduler](https://www.npmjs.com/package/scheduler) | 0.27.0 | MIT |

## Python production environment

| Package | Version | Declared license |
|---|---:|---|
| [aiosqlite](https://pypi.org/project/aiosqlite/) | 0.22.1 | MIT |
| [annotated-doc](https://pypi.org/project/annotated-doc/) | 0.0.4 | MIT |
| [annotated-types](https://pypi.org/project/annotated-types/) | 0.8.0 | MIT |
| [anyio](https://pypi.org/project/anyio/) | 4.14.2 | MIT |
| [attrs](https://pypi.org/project/attrs/) | 26.1.0 | MIT |
| [certifi](https://pypi.org/project/certifi/) | 2026.7.22 | MPL-2.0 |
| [charset-normalizer](https://pypi.org/project/charset-normalizer/) | 3.4.9 | MIT |
| [click](https://pypi.org/project/click/) | 8.4.2 | BSD-3-Clause |
| [distro](https://pypi.org/project/distro/) | 1.9.0 | Apache-2.0 |
| [fastapi](https://pypi.org/project/fastapi/) | 0.139.2 | MIT |
| [h11](https://pypi.org/project/h11/) | 0.16.0 | MIT |
| [idna](https://pypi.org/project/idna/) | 3.18 | BSD-3-Clause |
| [jsonpatch](https://pypi.org/project/jsonpatch/) | 1.33 | BSD-3-Clause |
| [jsonpointer](https://pypi.org/project/jsonpointer/) | 3.1.1 | BSD-3-Clause |
| [jsonschema](https://pypi.org/project/jsonschema/) | 4.25.1 | MIT |
| [jsonschema-specifications](https://pypi.org/project/jsonschema-specifications/) | 2025.9.1 | MIT |
| [langchain-core](https://pypi.org/project/langchain-core/) | 1.5.2 | MIT |
| [langchain-protocol](https://pypi.org/project/langchain-protocol/) | 0.0.18 | MIT |
| [langgraph](https://pypi.org/project/langgraph/) | 1.2.10 | MIT |
| [langgraph-checkpoint](https://pypi.org/project/langgraph-checkpoint/) | 4.1.1 | MIT |
| [langgraph-checkpoint-sqlite](https://pypi.org/project/langgraph-checkpoint-sqlite/) | 3.1.0 | MIT |
| [langgraph-prebuilt](https://pypi.org/project/langgraph-prebuilt/) | 1.1.0 | MIT |
| [langgraph-sdk](https://pypi.org/project/langgraph-sdk/) | 0.4.2 | MIT |
| [langsmith](https://pypi.org/project/langsmith/) | 0.10.11 | MIT |
| [librt](https://pypi.org/project/librt/) | 0.13.0 | MIT |
| [orjson](https://pypi.org/project/orjson/) | 3.11.9 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| [ormsgpack](https://pypi.org/project/ormsgpack/) | 1.12.2 | Apache-2.0 OR MIT |
| [packaging](https://pypi.org/project/packaging/) | 26.2 | Apache-2.0 OR BSD-2-Clause |
| [pydantic](https://pypi.org/project/pydantic/) | 2.13.4 | MIT |
| [pydantic-core](https://pypi.org/project/pydantic-core/) | 2.46.4 | MIT |
| [PyYAML](https://pypi.org/project/PyYAML/) | 6.0.3 | MIT |
| [referencing](https://pypi.org/project/referencing/) | 0.37.0 | MIT |
| [requests](https://pypi.org/project/requests/) | 2.34.2 | Apache-2.0 |
| [requests-toolbelt](https://pypi.org/project/requests-toolbelt/) | 1.0.0 | Apache-2.0 |
| [rpds-py](https://pypi.org/project/rpds-py/) | 2026.6.3 | MIT |
| [setuptools](https://pypi.org/project/setuptools/) | 82.0.1 | MIT |
| [sniffio](https://pypi.org/project/sniffio/) | 1.3.1 | MIT OR Apache-2.0 |
| [sqlite-vec](https://pypi.org/project/sqlite-vec/) | 0.1.9 | MIT AND Apache-2.0 |
| [starlette](https://pypi.org/project/starlette/) | 1.3.1 | BSD-3-Clause |
| [tenacity](https://pypi.org/project/tenacity/) | 9.1.4 | Apache-2.0 |
| [typing-inspection](https://pypi.org/project/typing-inspection/) | 0.4.2 | MIT |
| [typing-extensions](https://pypi.org/project/typing-extensions/) | 4.16.0 | PSF-2.0 |
| [urllib3](https://pypi.org/project/urllib3/) | 2.7.0 | MIT |
| [uuid-utils](https://pypi.org/project/uuid-utils/) | 0.17.0 | BSD-3-Clause |
| [uvicorn](https://pypi.org/project/uvicorn/) | 0.41.0 | BSD-3-Clause |
| [websockets](https://pypi.org/project/websockets/) | 15.0.1 | BSD-3-Clause |
| [xxhash](https://pypi.org/project/xxhash/) | 3.8.1 | BSD-2-Clause |
| [zstandard](https://pypi.org/project/zstandard/) | 0.25.0 | BSD-3-Clause |

## Asset boundary

The release bundle contains no externally fetched runtime fonts, images, or scripts. `apps/web/public/favicon.svg`, the static Pages tour, and tracked product screenshots are project-authored. The CC0 Scenario Kits are covered by the directory-specific notice described in [`LICENSE_SCOPE.md`](LICENSE_SCOPE.md).
