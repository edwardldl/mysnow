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

async function loadForecast() {
    showLoading();
    try {
        const { hrrrData, ecmwfData, location } = await fetchWeatherData(currentLocation, currentModelMode);

        const blendedData = blendForecasts(hrrrData, ecmwfData);
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

    const toggleBtn = document.getElementById('toggle-add-form');
    const form = document.getElementById('add-location-form');

    toggleBtn.addEventListener('click', () => {
        form.classList.toggle('hidden');
    });

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const name = document.getElementById('loc-name').value.trim();
        const lat = document.getElementById('loc-lat').value;
        const lon = document.getElementById('loc-lon').value;

        if (name && lat && lon) {
            const id = 'custom-' + Date.now();
            saveLocation(id, name, lat, lon);
            currentLocation = id;
            updateSwitcher();
            loadForecast();

            // reset form
            form.reset();
            form.classList.add('hidden');
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
    initLocListeners();

    document.getElementById('retry-btn').addEventListener('click', loadForecast);

    // Initial Load
    loadForecast();
}

// Start app
document.addEventListener('DOMContentLoaded', init);
