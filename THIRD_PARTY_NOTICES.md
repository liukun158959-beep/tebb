# Third-party notices

tebb's original code is licensed under the root MIT LICENSE. Third-party code,
dependencies, fonts and external assets retain their original copyright and
licenses; they are not relicensed by tebb's MIT license.

## Vendored components

- marked 15.0.12: desk_companion/ui/vendor/marked.min.js and
  pet-ui/public/vendor/marked.min.js. Source: https://github.com/markedjs/marked,
  full upstream terms (including Markdown attribution):
  licenses/vendored/marked-15.0.12-LICENSE.md.
- DOMPurify 3.2.6: the corresponding purify.min.js in both vendor directories.
  Source: https://github.com/cure53/DOMPurify. Dual Apache-2.0 OR MPL-2.0,
  full original text: licenses/vendored/DOMPurify-3.2.6-LICENSE.
- ZCOOL KuaiLe font: SIL Open Font License 1.1. The original notice is retained
  beside the font in desk_companion/ui/fonts/OFL.txt.
- Atlas is pinned to a committed source revision in scripts/prepare_runtime.py.
  Its MIT license and third-party notices, including OpenAI HumanEval's MIT task
  adaptations, are included under runtime/Lib/site-packages/atlas/licenses/.

## Release dependency notices

The portable release contains licenses/dependencies.json and original license
texts for the locked frontend and Windows Rust dependency graph under
licenses/frontend/ and licenses/rust/. Python packages keep their installed
license and attribution files in runtime/Lib/site-packages/; their actual
metadata is listed separately in the same inventory. Python's own license is
runtime/LICENSE.txt. SPDX expressions describe upstream packages, not one
blanket license for the application bundle. Lucide's full license includes
both its ISC notice and the Feather-derived icon MIT notice.

licenses/SOURCES.json records the source URL and SHA256 of supplemental
upstream notices where an installed package omitted its license text.
Supplemental notices are matched to the exact dependency version. They are
downloaded and reviewed when updating dependencies, not fetched at app startup.

The dlv upstream README declares MIT through an external license link that was
unavailable during review. Its original README and package declaration are
retained alongside a standard MIT text using the declared author, with no year
inferred. SOURCES.json identifies this reconstruction explicitly. The
react-remove-scroll-bar and proxy_tools snapshots retain their official upstream
license files where the published package omitted them; snapshot revisions are
recorded rather than represented as the unavailable npm gitHead or sdist content.
proxy_tools 0.1.0's installed metadata declares MIT, while its upstream LICENSE
retains Armin Ronacher's BSD notice. Both the reported metadata and that original
notice are preserved; the inventory's license field reports metadata and does
not override the included terms or resolve that upstream discrepancy.
MPL-licensed dependency sources can be obtained from the repository and exact
crate-download URLs in dependencies.json; their source licenses remain applicable.

## Assets and optional integrations

Live2D/Cubism Core, character models and user model weights are not included in
the public portable release. Loading support does not grant rights to those
assets. tebb's MIT code license does not grant third-party character, trademark
or external image rights. Release concept illustrations are AI-generated product
illustrations; prompts and generation records are in docs/VERSION_POSTER_PROMPT.md.
External services and user-supplied materials remain subject to their own terms.

## Windows installer

NSIS 3.11 and nsis-tauri-utils 0.5.3 are used by the Tauri installer. Their
original notices are retained in licenses/supplemental/installer and in the
generated dependency inventory. NSIS's COPYING describes its compression-module
licenses separately; the application MIT license does not replace those terms.
Exact component source locations and notice hashes are recorded alongside.
