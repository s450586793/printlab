# Third-party software in the PrintLab Docker image

PrintLab-authored code and documentation use PolyForm Noncommercial 1.0.0.
Keep [LICENSE.md](LICENSE.md) and both required notices in [NOTICE.txt](NOTICE.txt)
when redistributing PrintLab. Third-party components retain their own licenses,
copyright notices, and any license exceptions. The project license does not
replace those terms. Invoking an independent tool through a process does not by
itself establish that the whole workbench uses that tool's license.

## Components and version records

| Component | Docker build input | License and source |
| --- | --- | --- |
| Node.js | `node:22-bookworm-slim`; actual patch version recorded in the image | [Node.js](https://github.com/nodejs/node), MIT with bundled third-party notices in the distribution LICENSE |
| Python | `python:3.12-slim-bookworm`; actual patch version recorded in the image | [Python](https://www.python.org/downloads/source/), PSF license and bundled component notices |
| Codex CLI | `@openai/codex@0.169.0` by default; `CODEX_VERSION` is a build argument | [Codex](https://github.com/openai/codex/tree/rust-v0.169.0), Apache-2.0 and component notices supplied by its npm distribution |
| Bambu Studio | Official `02.09.01.70` Ubuntu 22 x86_64 AppImage; SHA-256 below | AGPL-3.0, with separate upstream component and resource terms; see the corresponding source section below |
| Blender and Debian runtime packages | Debian Bookworm apt packages, resolved during the build | [Blender](https://www.blender.org/about/license/), GPL-2.0-or-later; other packages retain their own terms and source records |
| CAD libraries | `cadquery>=2.6,<3`, `trimesh>=4,<5`, `numpy>=2,<3` | [CadQuery](https://github.com/CadQuery/cadquery), Apache-2.0; [trimesh](https://github.com/mikedh/trimesh), MIT; [NumPy](https://numpy.org/doc/stable/license.html), BSD-3-Clause; OCP/Open CASCADE and transitive libraries retain their own licenses and exceptions |
| Workbench Python dependencies | `Harness/pyproject.toml`, including FastAPI, Uvicorn, HTTPX, Pillow, portalocker, websocket-client, pypdf, python-docx and openpyxl | Each installed distribution retains its metadata and supplied license files in `/opt/venv`; use the exact installed versions for source and license review |
| Web application dependencies | Exact npm versions from `Harness/studio/package-lock.json` | React, react-dom, Three.js and react-markdown use MIT; Lucide uses ISC; transitive and build dependencies retain their individual terms |

The base image tags, apt repositories and Python version ranges can resolve to
different patch versions on later builds. They are not an exact inventory of an
already published image. Pin an image digest when reproducing a deployment.

## Notices and inventories inside the image

The image retains upstream files and adds these records:

- `/usr/share/doc/printlab/`: this document, PrintLab's license and notices,
  `node-version.txt`, `python-version.txt`, `codex-version.txt`,
  `python-packages.json`, `debian-packages.tsv` and `studio-package-lock.json`.
  The Debian TSV columns are binary package, binary version, source package
  and source version.
- `/usr/share/doc/printlab/third-party/node/LICENSE`: the Node distribution
  license, including its bundled third-party notices.
- `/usr/share/doc/printlab/third-party/studio/`: license, copying, notice and
  copyright files supplied by the npm dependencies used during the web build.
  This includes build tools, not only code incorporated into the final bundle.
- `/opt/codex/node_modules/`: the Codex npm distribution, its platform packages,
  package metadata and supplied license files.
- `/opt/bambu-studio/`: the unmodified extracted AppImage contents, retained
  upstream resources and notices, `LICENSE` and `PRINTLAB-SOURCE.json`.
- `/usr/share/doc/*/copyright`: Debian package copyright and license records.

The GitHub Docker workflow requests a BuildKit SBOM and provenance attestation
for published images. These records help identify the actual software shipped;
an SBOM is not a substitute for license texts or corresponding source.

## Bambu Studio corresponding source

The pinned AppImage SHA-256 is
`e0dfb6dafe0181b6bf8635bbb9a7b106f02f2343f8e41949f1d156b3f36241e5`.
The image runs the official executable as a separate CLI process and does not
modify it. The source for the designated upstream version is available from:

- [Official release v02.09.01.70](https://github.com/bambulab/BambuStudio/releases/tag/v02.09.01.70).
- [Source tree, build instructions and dependency definitions](https://github.com/bambulab/BambuStudio/tree/v02.09.01.70).
- [Source archive for that tag](https://github.com/bambulab/BambuStudio/archive/refs/tags/v02.09.01.70.tar.gz).
- [AGPL license text included by PrintLab](deploy/bambu-studio.LICENSE).

Redistributors remain responsible for providing access to the complete
Corresponding Source required by the applicable licenses, including relevant
build scripts and covered bundled dependencies. Verify that the designated
source remains available and corresponds to the distributed binary; preserve
or provide an equivalent source location if the upstream links become
unavailable. A binary download URL alone does not meet that source obligation.

For Debian-distributed software, use the source package and version recorded
in `debian-packages.tsv` with the [Debian Sources archive](https://sources.debian.org/).
For Python wheels, Codex and other bundled components, review their exact
version's upstream source and supplied notices before redistributing a changed
image. This document records the distribution method and source locations;
it does not claim that an unbuilt or modified image has completed a license audit.
