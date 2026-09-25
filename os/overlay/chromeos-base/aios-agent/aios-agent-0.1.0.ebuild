# Copyright 2026 Martial Zinsou
# Distributed under the terms of the BSD 3-Clause License

EAPI=8

DESCRIPTION="aiOS agentic on-device assistant service"
HOMEPAGE="https://github.com/martialzinsou/aios"
LICENSE="BSD"
SRC_URI=""
S="${WORKDIR}"

SLOT="0"
KEYWORDS="~amd64 ~arm64"
IUSE=""

# Chromium OS ships python3 as dev-lang/python with a version slot that moves
# with the branch.  Leave it unslotted so the package resolves on any branch;
# tighten to `dev-lang/python:3.11` (or whatever your branch uses) if your
# overlay enforces slot dependencies.
RDEPEND="dev-lang/python"
DEPEND="${RDEPEND}"

src_unpack() {
	default
	# The agent is vendored into files/ by os/scripts/20-prepare-overlay.sh so
	# the build never needs network access or a DISTDIR mirror.
	cp -a "${FILESDIR}/aios_agent" "${S}/" || die "failed to stage agent source"
	cp -a "${FILESDIR}/README.md" "${S}/" 2>/dev/null || true
}

src_compile() {
	# Pure Python, zero runtime dependencies: nothing to compile.
	:
}

src_install() {
	local readme
	readme="${S}/README.md"
	[[ -f ${readme} ]] && dodoc "${readme}"

	# Importable tree: /usr/lib/aios/aios_agent/…
	insinto /usr/lib/aios
	doins -r aios_agent

	# CLI entry point + service request client
	exeinto /usr/bin
	doexe "${FILESDIR}/aios" "${FILESDIR}/aios-request"

	# Declarative security policy, overridable by the admin
	insinto /usr/share/aios
	doins "${FILESDIR}/aios-policy.json"

	# Upstart job (Chromium OS init)
	insinto /etc/init
	doins "${FILESDIR}/aios-agent.conf"

	# State directory for the audit log + episodic memory
	keepdir /var/lib/aios
}

pkg_postinst() {
	elog "aiOS agent installed."
	elog "  start : start aios-agent"
	elog "  chat  : aios chat          (interactive, always confirms)"
	elog "  req   : aios-request       (JSON lines over the agent socket)"
	elog "  policy: /usr/share/aios/aios-policy.json"
	elog ""
	elog "The shipped upstart job runs FAIL-CLOSED (--no-confirm): only READ"
	elog "operations execute without a human in the loop.  Wire a GUI Confirmer"
	elog "in the session before relaxing this."
}
