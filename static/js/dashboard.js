const {
    dailyCounts,
    topContributors,
    topEditors,
    topImageries,
    topContributorsTime,
    topEditorsTime,
    topImageriesTime,
} = window.dashboardData;

const modernPalette = [
    'rgba(54, 162, 235, 0.8)',  // Blue
    'rgba(255, 99, 132, 0.8)',  // Pink
    'rgba(75, 192, 192, 0.8)',  // Teal
    'rgba(255, 206, 86, 0.8)',  // Yellow
    'rgba(153, 102, 255, 0.8)', // Purple
    'rgba(255, 159, 64, 0.8)',  // Orange
    'rgba(76, 175, 80, 0.8)',   // Green
    'rgba(233, 30, 99, 0.8)'    // Red
];

// Changesets Over Time Chart
if (dailyCounts && dailyCounts.length > 0) {
    new Chart(document.getElementById('changesetChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: dailyCounts.map(item => new Date(item.date).toLocaleDateString()),
            datasets: [{
                label: 'Changesets per Day',
                data: dailyCounts.map(item => item.count),
                borderColor: 'rgba(54, 162, 235, 1)',
                backgroundColor: 'rgba(54, 162, 235, 0.2)',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            scales: { y: { beginAtZero: true } }
        }
    });
} else {
    console.error('No data available for the changeset chart');
}

// Top Contributors Chart
if (topContributors && topContributors.length > 0) {
    new Chart(document.getElementById('topContributorsChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: topContributors.map(item => item.user),
            datasets: [{
                label: 'Changesets',
                data: topContributors.map(item => item.count),
                backgroundColor: 'rgba(255, 99, 132, 0.8)',
                borderColor: 'rgba(255, 99, 132, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            scales: { y: { beginAtZero: true } }
        }
    });
} else {
    console.error('No data available for the top contributors chart');
}

// Top Imageries Chart
if (topImageries && topImageries.length > 0) {
    new Chart(document.getElementById('topImageriesChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: topImageries.map(item => item.imagery),
            datasets: [{
                label: 'Changesets',
                data: topImageries.map(item => item.count),
                backgroundColor: 'rgba(75, 192, 192, 0.8)',
                borderColor: 'rgba(75, 192, 192, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            scales: { y: { beginAtZero: true } }
        }
    });
} else {
    console.error('No data available for the top imageries chart');
}

// Top Editors Chart
if (topEditors && topEditors.length > 0) {
    new Chart(document.getElementById('topEditorsChart').getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: topEditors.map(item => item.editor),
            datasets: [{
                label: 'Changesets',
                data: topEditors.map(item => item.count),
                backgroundColor: modernPalette,
                borderColor: modernPalette.map(color => color.replace('0.8', '1')),
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            scales: { y: { beginAtZero: true } }
        }
    });
} else {
    console.error('No data available for the top editors chart');
}

// Top Contributors Time Chart
if (topContributorsTime && topContributorsTime.users && topContributorsTime.users.length > 0) {
    new Chart(document.getElementById('topContributorsTimeChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: topContributorsTime.dates,
            datasets: topContributorsTime.users.map((user, index) => ({
                label: user.name,
                data: user.counts,
                backgroundColor: `rgba(255, 99, 132, ${0.8 - (index * 0.1)})`,
                borderColor: 'rgba(255, 99, 132, 1)',
                borderWidth: 1
            }))
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { stacked: true },
                y: { stacked: true, beginAtZero: true }
            }
        }
    });
}

// Top Imageries Time Chart
if (topImageriesTime && topImageriesTime.imageries && topImageriesTime.imageries.length > 0) {
    new Chart(document.getElementById('topImageriesTimeChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: topImageriesTime.dates,
            datasets: topImageriesTime.imageries.map((imagery, index) => ({
                label: imagery.name,
                data: imagery.counts,
                backgroundColor: `rgba(75, 192, 192, ${0.8 - (index * 0.1)})`,
                borderColor: 'rgba(75, 192, 192, 1)',
                borderWidth: 1
            }))
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { stacked: true },
                y: { stacked: true, beginAtZero: true }
            }
        }
    });
}

// Top Editors Time Chart
if (topEditorsTime && topEditorsTime.editors && topEditorsTime.editors.length > 0) {
    new Chart(document.getElementById('topEditorsTimeChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: topEditorsTime.dates,
            datasets: topEditorsTime.editors.map((editor, index) => ({
                label: editor.name,
                data: editor.counts,
                backgroundColor: modernPalette[index % modernPalette.length],
                borderColor: modernPalette[index % modernPalette.length].replace('0.8', '1'),
                borderWidth: 1
            }))
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { stacked: true },
                y: { stacked: true, beginAtZero: true }
            }
        }
    });
}
