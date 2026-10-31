# Favicon

The site has no favicon: browser tabs and bookmarks show the browser's default icon. It needs an
actual design (something OSM-flavored that reads at 16 px), then the icon files in `static/` and
a `<link rel="icon">` in `base.html` (and `changesets.html`, which does not extend it) under
`changesets/templates/changesets/`.
