# Debian distribution model

One Debian source package (`debian/`, source format `3.0 (quilt)`) builds:

- `sectorrec_<version>_amd64.deb`: desktop on AMD64.
- `sectorrec_<version>_arm64.deb`: the same desktop on ARM64.
- `sectorrec-cardputer_<version>_arm64.deb`: Cardputer Zero framebuffer edition.

All packages are built with **Debhelper + pybuild**, not by hand-assembling
`dpkg-deb` trees. All recovery engines are provided by Debian APT packages;
none is embedded in the .deb. The two application flavours are mutually
exclusive on an ARM64 machine because they provide the same `/usr/bin/sectorrec`.

Both Debian editions use `pkexec`/Polkit with fixed, root-owned, input-validated
helpers, not a privileged interface or `sudoers` rule. AppImage retains its
interactive `sudo`/Polkit authorization and is not affected.

**Desktop (AMD64/ARM64)**: SectorRec first uses the user's existing
permissions (including UDisks2 and direct raw reads where allowed). The first
protected operation starts a single Polkit-authenticated, root-owned session
broker; it can perform ONLY the three installed, device-validating SectorRec
helpers. Existing subprocess streams, cancellation and read-only mount guards
remain available. Further protected operations share this broker without
another authorization dialog. The broker exits when the GUI exits. No user
rules, password caches, group enrollment, re-login or permanent privileges are
installed. A Debian maintainer must review this privileged protocol.

When upgrading from SectorRec 1.0-7/1.0-8, the packaging removes only the
previously generated SectorRec per-user Polkit rules with the exact expected
bootstrap marker, preserving administrator-authored rules.

**Cardputer Zero**: its package sets passwordless authorization on its two
fixed-purpose helpers (reader + backlight) for all sessions, including those
without a Polkit desktop agent. This is intentionally less restrictive and
is suitable only for trusted, single-user handheld devices. Do NOT copy this
policy into the general-purpose desktop package.

## Build on native Debian

```sh
sudo apt install build-essential devscripts debhelper dh-python \
  pybuild-plugin-pyproject python3-all python3-setuptools python3-wheel \
  python3-pytest python3-pytest-timeout python3-pil python3-tk xvfb xauth \
  sleuthkit testdisk gddrescue smartmontools pkexec lintian
./build-deb.sh "$(dpkg --print-architecture)" all
# or build the source package directly with dpkg-buildpackage after creating
# a matching sectorrec_1.0.orig.tar.gz alongside the source tree.
```

## Before uploading to the official archive

- Replace `noreply@example.invalid` in `debian/control` and `debian/changelog`
  with a **real public maintainer identity**.
- Audit each upstream-owned file/image in `src/assets` and `docs/images` for
  copyright and licensing. `debian/copyright` must accurately list exceptions.
- Build and run `lintian -i -I --pedantic`, `appstreamcli validate`, and test
  installation/removal and privilege prompts on a clean Debian system.
- Validate privileged access on desktop with a fresh installation, no logout,
  and that a second launch requests new authorization if necessary; verify shutdown.
- Test Cardputer Zero headless Polkit privilege access on a real device.
- Find an experienced Debian sponsor (e.g. mentors.debian.net) and review the
  package before publication. A successful GitHub CI build is not acceptance.

See https://www.debian.org/doc/debian-policy/ and
https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
