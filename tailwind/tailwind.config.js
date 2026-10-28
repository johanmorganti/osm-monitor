// Tailwind v3 build for every page (static/css/site.css, built in the Dockerfile's css stage).
// Default theme and no plugins: the same CSS the Play CDN generated before
// (docs/decisions/self-hosted-assets.md). Classes are found by scanning these files, so a class
// must appear whole in them: build names like `bg-${color}-500` and they are silently missing.
module.exports = {
    content: ['./changesets/templates/**/*.html', './static/js/**/*.js'],
    theme: { extend: {} },
    plugins: [],
};
