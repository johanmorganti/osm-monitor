# Vendored front-end libraries

Served from the site itself (whitenoise) instead of a CDN, so no third-party host can change the
code that runs on the pages. Why: [self-hosted-assets](../../docs/decisions/self-hosted-assets.md).

Each directory is one npm package at one version, unpacked from the registry tarball after
checking its `sha512` against the registry's `dist.integrity`. The files are byte-identical to the
package except for the trailing `sourceMappingURL` comment, removed from the four files marked
below: the `.map` files aren't shipped, and Django's hashed static storage fails `collectstatic` on
a reference to a missing file. Each directory keeps the package's license.

| Directory | npm package | Files |
|---|---|---|
| `chart.js-4.5.1` | `chart.js@4.5.1` | `dist/chart.umd.min.js` (map comment removed) |
| `leaflet-1.9.4` | `leaflet@1.9.4` | `dist/leaflet.js` (map comment removed), `dist/leaflet.css`, `dist/images/*` |
| `maplibre-gl-5.24.0` | `maplibre-gl@5.24.0` | `dist/maplibre-gl.js` (map comment removed), `dist/maplibre-gl.css` |
| `maplibre-gl-leaflet-0.1.4` | `@maplibre/maplibre-gl-leaflet@0.1.4` | `leaflet-maplibre-gl.js` |
| `swagger-ui-dist-5.33.2` | `swagger-ui-dist@5.33.2` | `swagger-ui.css` (map comment removed), `swagger-ui-bundle.js`, `swagger-ui-standalone-preset.js`, `favicon-32x32.png` |

To update one: download the new tarball, check its integrity, copy the same files into a new
`<package>-<version>` directory, remove the map comments, update the `{% static %}` paths (and,
for Swagger UI, `SWAGGER_UI_VERSION_DIR` in `changesets/views.py`), and delete the old directory.
Use a version released at least two weeks ago.

sha256 of the files as committed:

```
84d0e233daba702b8f77d669d8c137cad36d441a10f200b6f2d3ab553bdfcf6b  chart.js-4.5.1/chart.umd.min.js
066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf  leaflet-1.9.4/images/layers-2x.png
1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6  leaflet-1.9.4/images/layers.png
00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528  leaflet-1.9.4/images/marker-icon-2x.png
574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437  leaflet-1.9.4/images/marker-icon.png
264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da  leaflet-1.9.4/images/marker-shadow.png
a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6  leaflet-1.9.4/leaflet.css
dc71f8a6880bc3ca1bd9fa8dc5f1af48c702dc510b0a78240a07c5feed7ce935  leaflet-1.9.4/leaflet.js
ab1e70d59ec40465bae7e7030da2f3ccf28133fd502e62bd598eefbadfd7a732  maplibre-gl-5.24.0/maplibre-gl.css
8b2c6bfadb8cc2d720aea2816ac368e51ab75dbb81ee3fac2f8b97256ebd1585  maplibre-gl-5.24.0/maplibre-gl.js
1e6cf8cb3eb5fd909879aa1bf36a383fb506c9a5b2dbbfababce65a294dd1fcb  maplibre-gl-leaflet-0.1.4/leaflet-maplibre-gl.js
3ed612f41e050ca5e7000cad6f1cbe7e7da39f65fca99c02e99e6591056e5837  swagger-ui-dist-5.33.2/favicon-32x32.png
6b3bfcaff3fc82c0e6992ca4b370c4f22970dec35fff60d229302b3ee7688715  swagger-ui-dist-5.33.2/swagger-ui-bundle.js
5243d492e14505e0cab87ac8b0195d0e615943e651743b2b698450a46eb470be  swagger-ui-dist-5.33.2/swagger-ui-standalone-preset.js
23ae95db3e810a3e1eb7d8995ddb587dd463c25d0c06bbd768d58ec8b08f6981  swagger-ui-dist-5.33.2/swagger-ui.css
```
