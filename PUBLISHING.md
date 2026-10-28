# Publication and release status

The repository is public. Version 0.5.0a1 is experimental software for reviewing existing
Velociraptor evidence through GUI, CLI and MCP interfaces.

## Licensing

A project open-source license has not been selected. Public source visibility does not
select a license or establish redistribution rights. See NOTICE and ACKNOWLEDGEMENTS.md.
The private-package classifier in pyproject.toml prevents accidental PyPI publication;
it does not describe the repository's visibility.

The inherited provenance record states that project modules were implemented separately
from mgreen27/mcp-velociraptor, with compatibility at the interface level. That upstream
repository had no license file at the recorded review. This record is not an independent
provenance audit. Keep attribution and review any upstream-derived material before
redistribution. A new repository or rewritten history does not establish those rights.

## Public materials

Describe the implemented evidence-review workflow, shared interfaces, saved evidence and
report exports. Keep model output framed as proposals for human review. Performance and
citation quality remain under evaluation; VALIDATION.md distinguishes automated tests from
operator-reported live checks.

Use only synthetic or approved redacted illustrations. Keep credentials, real evidence,
host/client inventories, private notebook exports, runtime logs and model requests out of
source, screenshots and release artifacts.

## Packaged releases

The lab's complete offline bundle has not been attached to a GitHub Release. The README
provides a source-install path available from this public repository.

Before publishing a tagged binary distribution, select the project license, review code
provenance and the staged tree, preserve dependency notices, and verify the extracted
artifact. Record the source commit, checksums, tested environments and known limits.
The current PyPI upload guard remains intentional.
