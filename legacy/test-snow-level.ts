import { fetchWeatherData } from './src/api';
import { blendForecasts, groupData } from './src/data';

async function test() {
    const { hrrrData, ecmwfData, location } = await fetchWeatherData('palisades', 'best_match');
    const blendedData = blendForecasts(hrrrData, ecmwfData, location, 'sierra_custom');
    const firstPoint = blendedData.hourly[0];
    console.log("First point precip:", firstPoint.precipitation);
    console.log("First point RH:", firstPoint.rh);
    console.log("First point Freezing Level (from source):", ecmwfData.hourly.freezing_level_height[0]);
    console.log("First point calc SnowLevel:", firstPoint.snowLevel);
}
test().catch(console.error);
