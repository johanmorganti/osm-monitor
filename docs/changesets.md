# This is a test for changsets

More will come soon

```js
const changesets = FileAttachment("./data/changesets.json").json();
```

```js
display(changesets);
```

```js

display(
    Plot.plot({
        title: "Change count",
    x: {},
    y: {},
    marks: [
        Plot.dot(changesets, {
        x: "created_at",
        y: "changes_count",
        z: null, // varying color, not series
        })
    ]
  })
);
```