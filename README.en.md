# PrintLab

English · [简体中文](README.md)

Turn an idea into something you can hold.

PrintLab is a Docker-based 3D-printing workbench for **amd64 (x86_64)** Linux / NAS hosts. Chat with Codex on the left and preview models and revisions on the right. Requirements, reference images, attachments, source models, and slicing results stay together in each project. Configure your own model API in the web settings; Home Assistant is optional.

## Docker quick start

Install Docker and the Compose plugin, then run:

```sh
git clone https://github.com/s450586793/printlab.git PrintLab
cd PrintLab
cp .env.example .env
docker compose pull
docker compose up -d
```

Compose uses `ghcr.io/s450586793/printlab:latest` and exposes <http://127.0.0.1:8018> on the host's loopback interface by default. For NAS or remote access, set `PRINTLAB_BIND_IP` and `PRINTLAB_PORT` in `.env`, and set `PRINTLAB_STUDIO_URL` to the actual browser URL before starting the service. Data is stored in `./deploy/data` by default; change it with `PRINTLAB_DATA_DIR`.

On the first visit, open <http://127.0.0.1:8018/setup> and read the initialization code in the deployment terminal:

```sh
docker compose exec printlab cat /data/Runtime/private/setup-token
```

Enter the code, an administrator username, and a password of at least **12 characters** in the browser, then sign in. No pre-created secret file is required. Accounts and projects persist across restarts and image updates. See the [Docker deployment guide](docs/DOCKER.md) for ports, LAN access, reverse proxies, migration, backups, and source builds. Detailed reference guides currently use Simplified Chinese.

## Configure a model and create a project

Open Settings → Model services in the sidebar. Enter a service name, API Base URL, API Key, and model IDs. The Base URL may include `/v1` or a gateway path, but must not include `/responses`. Test function tools, save the service, then select a service and model in the project composer. You can add multiple services.

The model service must support **OpenAI Responses and function tools**. Image understanding also requires an image-capable model. Codex inside the container reads and writes project files, runs CadQuery / Blender, and exports models. Configure a working service before submitting modeling tasks.

Choose “New idea” and describe the purpose and dimensions. Add reference images or attachments alongside your message. Select, drag, or paste PNG, JPG, or WebP images; use the attachment picker for STL, STEP, 3MF, PDF, Word documents, spreadsheets, ZIP archives, and source files. Files persist with the conversation. Archives are not extracted automatically.

## The workbench

- **Chat and preview together:** view GLB, STL, and 3MF models, select revisions, and download files while discussing changes. On phones, the preview appears below the conversation.
- **Persistent project context:** retain requirements, images, attachments, source files, execution records, and handoffs. Stop or resume work; refreshing the page does not resubmit tasks.
- **Modeling and slicing tools:** the image includes Codex, CadQuery, Blender, and Bambu Studio CLI, shared by projects inside the container.
- **Projects and print preparation:** organize revisions, materials, printer assignments, slicing results, parts lists, and physical feedback.
- **Optional device connection:** use Home Assistant to view Bambu printer status, AMS loading, and camera snapshots.

A typical workflow is: purpose and dimensions → visual references → CAD → inspect and revise → materials → slicing comparison → check print files → physical feedback. The original collaboration workflow and role prompts remain available. [Workflow](Context/workflow.md) · [Role prompts](docs/ROLES.md)

## Built-in slicing and device support

The Linux image includes Bambu Studio `02.09.01.70` CLI and its offline slicing resources. Automatic profile selection currently supports **A1 / P1P, a 0.4 mm nozzle, single-color Generic PLA / PETG, 0.20 mm Standard, and Textured PEI Plate**. Assign the current revision to a printer under Print preparation, then ask the project's Codex to slice it. Results include a downloadable `job.gcode.3mf`, time, and material estimates.

Other nozzles, materials, multicolor setups, and plates require separate profile verification. Successful offline slicing means the files passed slicing and file checks; the physical nozzle, filament supply, and plate still need inspection. **Automatic print submission through Home Assistant is not enabled.** See [Offline slicing](docs/DOCKER.md#内置离线切片) for commands and limitations.

For device status, configure `ha-bambulab` in your Home Assistant, then open Settings → Home Assistant in PrintLab. Enter the HA URL and a long-lived access token, test, and save. Modeling, previews, and offline slicing work without HA. See [Home Assistant connection](docs/DOCKER.md#可选-home-assistant-接入) for connection and removal details.

## Updates and development

Run `docker compose pull` and `docker compose up -d` in the repository directory to update the image. Back up the data directory first. You can pin an image version or digest, or build from source with `compose.build.yaml`. [Updates and source builds](docs/DOCKER.md#镜像更新与源码构建)

The source includes the workbench, original workflow documents, role prompts, and empty project templates. [Dependencies and versions](docs/DEPENDENCIES.md) · [Third-party licenses and source links](THIRD_PARTY_NOTICES.md) · [Verification record](docs/VERIFICATION.md)

## Desktop installation and original workflow

The original Mac / Windows installation and desktop workflow guides remain available: [Installation](docs/SETUP.md) · [Windows](docs/WINDOWS.md) · [Interaction guide](docs/GETTING_STARTED.md) · [Agent onboarding](docs/AGENT_START.md). Desktop printing capabilities differ from the Docker environment. [Printing and compatibility](docs/PRINTING.md)

## License and attribution

This project is based on [PrintLab](https://github.com/AFangTeam/PrintLab) by **AFangTeam / 阿方 (Eric)**, the 3D-printing workbench and collaboration workflow featured in his E41 video. This repository provides general Docker deployment and web configuration.

PrintLab-authored source and documentation use **PolyForm Noncommercial 1.0.0**. Use, modification, and redistribution are permitted for the noncommercial purposes defined by that license. Redistribution must include the license terms and required attribution from `NOTICE.txt`. Commercial use requires separate authorization from AFangTeam.

Third-party software retains its own license. [License](LICENSE.md) · [Required notices](NOTICE.txt)
