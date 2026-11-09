# My Island — Windows updates

This repository distributes My Island Windows installation files and automatic-update metadata. The application source repository remains private.

## 다운로드

**[Windows 설치 파일 받기 (0.2.1)](https://github.com/chanwhi123/My-Island-Updates/releases/download/v0.2.1/My-Island-0.2.1-Windows-x64.exe)** · [최신 릴리스](https://github.com/chanwhi123/My-Island-Updates/releases/latest)

기존 앱은 이 설치 파일로 최초 한 번 전환합니다. 이후 새 버전은 앱이 자동 확인·다운로드하고, **다시 시작하여 설치**를 누르면 설치됩니다. 설정의 **소프트웨어 업데이트**에서 확인할 수 있습니다.

Chrome 개발자 모드 확장의 자동 업데이트는 별도 웹 스토어 등록 후 지원됩니다.

Automatic update feed:

```text
https://github.com/chanwhi123/My-Island-Updates/releases/latest/download/
```

The installer-only releases contain a Windows x64 EXE, its differential download block map, and `latest.yml`. Existing app version 0.1.0 needs a one-time installation of an update-capable version. ZIP extraction builds do not automatically convert to installed builds.

## Publishing a tested build

The release operator validates the private source build's workflow, successful result, main branch, and commit before dispatching **Publish Windows update**. Dispatch inputs include the exact validated source commit, a short-lived signed URL and SHA256 for the installer-only artifact. This artifact must not contain a source archive. The public workflow checks the archive digest, selects only the installer and update metadata, verifies SHA512, and rejects overwritten or older versions. It uses this repository's short-lived `GITHUB_TOKEN`; no permanent cross-repository credential is stored in the app.

The operator must dispatch each approved release. This workflow does not poll a private repository or automatically publish every private source commit. Once a release is published, installed app clients discover it automatically through the feed.

Do not place source archives, signing keys, browser pairing codes, GitHub tokens, or customer data in an artifact supplied to this workflow. Ordinary workflow access is restricted by GitHub repository write permissions. The signed artifact URL is read from the event payload, masked before download, and never deliberately logged. It expires shortly after dispatch.

Current development installers may be unsigned. Downloading an installer does not grant it permission to run automatically. The app downloads a new version and offers an explicit restart-and-install action.
