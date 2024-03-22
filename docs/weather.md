# Weather report

```js
const forecast = FileAttachment("./data/forecast.json").json();
```


```js
display(forecast);

function temperaturePlot(data, {width} = {}) {
  return Plot.plot({
    title: "Hourly temperature forecast",
    width,
    x: {type: "utc", ticks: "day", label: null},
    y: {grid: true, inset: 10, label: "Degrees (F)"},
    marks: [
      Plot.lineY(data.properties.periods, {
        x: "startTime",
        y: "temperature",
        z: null, // varying color, not series
        stroke: "temperature",
        curve: "step-after"
      })
    ]
  });
}
```

test write

```js
display(temperaturePlot(forecast));
```

<div class="grid grid-cols-2">
  <div class="card">${resize((width) => temperaturePlot(forecast, {width}))}</div>
  <div class="card">${resize((width) => temperaturePlot(forecast, {width}))}</div>
</div>

<div class="grid grid-cols-3">
  <div class="card">${resize((width) => temperaturePlot(forecast, {width}))}</div>
</div>