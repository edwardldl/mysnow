import { getLocations, saveLocation, removeLocation, fetchWeatherData } from './api.js';
import { blendForecasts, groupData } from './data.js';
import {
    showLoading,
    showError,
    showContent,
    renderHeader,
    renderDaySummaries,
    renderDayDetail,
    renderLocationSwitcher
} from './render.js';
import { initSnowEngine } from './utils.js';

let currentLocation = 'palisades';
let currentModelMode = 'best_match';
let currentDaysData = [];
let allSkiAreas = [];

async function loadSkiAreas() {
    try {
        const response = await fetch('./ski_areas.csv');
        const csvText = await response.text();
        const lines = csvText.split('\n');
        const headers = lines[0].split(',');

        const nameIdx = headers.indexOf('name');
        const countryIdx = headers.indexOf('countries');
        const regionIdx = headers.indexOf('regions');
        const latIdx = headers.indexOf('lat');
        const lonIdx = headers.indexOf('lng');

        // Robust CSV line parser that handles quoted commas
        const parseCSVLine = (line) => {
            const result = [];
            let cell = '';
            let inQuotes = false;
            for (let i = 0; i < line.length; i++) {
                const char = line[i];
                if (char === '"') {
                    inQuotes = !inQuotes;
                } else if (char === ',' && !inQuotes) {
                    result.push(cell.trim());
                    cell = '';
                } else {
                    cell += char;
                }
            }
            result.push(cell.trim());
            return result;
        };

        allSkiAreas = lines.slice(1).map(line => {
            if (!line.trim()) return null;
            const cells = parseCSVLine(line);
            if (cells.length < headers.length) return null;

            return {
                name: cells[nameIdx].replace(/^"|"$/g, ''),
                country: cells[countryIdx].replace(/^"|"$/g, ''),
                region: cells[regionIdx].replace(/^"|"$/g, ''),
                lat: cells[latIdx],
                lon: cells[lonIdx]
            };
        }).filter(area => area && area.name && area.lat && area.lon);
    } catch (err) {
        console.error("Failed to load ski areas CSV:", err);
    }
}

async function loadForecast() {
    showLoading();
    try {
        const { hrrrData, ecmwfData, location } = await fetchWeatherData(currentLocation, currentModelMode);

        const blendedData = blendForecasts(hrrrData, ecmwfData, location);
        currentDaysData = groupData(blendedData).slice(0, 14);

        // Find current conditions (first hour in the data)
        const currentData = currentDaysData.length > 0 && currentDaysData[0].hourly.length > 0
            ? currentDaysData[0].hourly[0]
            : null;

        renderHeader(location, currentData);
        renderDaySummaries(currentDaysData, (day) => {
            renderDayDetail(day);
        });

        // Update model description in UI
        const modelLegend = document.querySelector('.model-legend');
        if (modelLegend) {
            if (currentModelMode === 'best_match') {
                modelLegend.innerHTML = `<span class="badge model-mixed">BEST MATCH</span> Automatically selects the best available local models.`;
            } else {
                modelLegend.innerHTML = `
                    <span class="badge model-hrrr">HRRR</span> 0-48 hours (3km resolution)
                    <br>
                    <span class="badge model-ecmwf">ECMWF</span> 3-16 days (9km resolution)
                `;
            }
        }

        showContent();
    } catch (err) {
        console.error(err);
        showError('Failed to load forecast data. Please try again later.');
    }
}

function updateSwitcher() {
    renderLocationSwitcher(
        getLocations(),
        currentLocation,
        (id) => {
            currentLocation = id;
            updateSwitcher();
            loadForecast();
        },
        (id) => {
            if (currentLocation === id) currentLocation = 'palisades';
            removeLocation(id);
            updateSwitcher();
            if (currentLocation === 'palisades') loadForecast();
        }
    );
}

function initLocListeners() {
    updateSwitcher();

    const searchInput = document.getElementById('ski-area-search');
    const searchResults = document.getElementById('search-results');
    const coordsInput = document.getElementById('loc-coords');
    const addBtn = document.getElementById('add-location-btn');

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase().trim();

        if (query.length < 2) {
            searchResults.innerHTML = '';
            searchResults.classList.add('hidden');
            return;
        }

        const matches = allSkiAreas
            .filter(area => area.name.toLowerCase().includes(query))
            .slice(0, 10);

        if (matches.length > 0) {
            searchResults.innerHTML = matches.map(area => `
                <div class="search-item" data-name="${area.name}" data-lat="${area.lat}" data-lon="${area.lon}">
                    <span class="search-item-name">${area.name}</span>
                    <span class="search-item-meta">${area.region ? area.region + ', ' : ''}${area.country}</span>
                </div>
            `).join('');
            searchResults.classList.remove('hidden');
        } else {
            searchResults.innerHTML = '<div class="search-item">No matches found</div>';
            searchResults.classList.remove('hidden');
        }
    });

    searchResults.addEventListener('click', (e) => {
        const item = e.target.closest('.search-item');
        if (!item || !item.dataset.lat) return;

        const { name, lat, lon } = item.dataset;
        
        searchInput.value = name;
        coordsInput.value = `${lat}, ${lon}`;
        
        searchResults.innerHTML = '';
        searchResults.classList.add('hidden');
    });

    addBtn.addEventListener('click', () => {
        const name = searchInput.value.trim();
        const coordsRaw = coordsInput.value.trim();

        let lat, lon;
        const coords = coordsRaw.split(/[\s,]+/);
        if (coords.length >= 2 && !isNaN(coords[0]) && !isNaN(coords[1])) {
            lat = coords[0];
            lon = coords[1];
        }

        if (lat && lon) {
            const displayName = name || `${parseFloat(lat).toFixed(2)}, ${parseFloat(lon).toFixed(2)}`;
            const id = 'loc-' + Date.now();
            saveLocation(id, displayName, lat, lon);
            currentLocation = id;
            
            searchInput.value = '';
            coordsInput.value = '';
            
            updateSwitcher();
            loadForecast();
        } else {
            alert('Please provide valid coordinates (lat, lon).');
        }
    });

    // Close search results when clicking outside
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
            searchResults.classList.add('hidden');
        }
    });

    // Model Toggle
    const modelToggle = document.getElementById('model-toggle');
    const toggleTexts = document.querySelectorAll('.toggle-text');

    modelToggle.addEventListener('change', () => {
        currentModelMode = modelToggle.checked ? 'hrrr_ecmwf' : 'best_match';

        // Update active class for texts
        toggleTexts.forEach(txt => {
            if (txt.dataset.mode === currentModelMode) {
                txt.classList.add('active');
            } else {
                txt.classList.remove('active');
            }
        });

        loadForecast();
    });
}

function init() {
    initSnowEngine();
    loadSkiAreas();
    initLocListeners();

    document.getElementById('retry-btn').addEventListener('click', loadForecast);

    // Initial Load
    loadForecast();
}

// Start app
document.addEventListener('DOMContentLoaded', init);
