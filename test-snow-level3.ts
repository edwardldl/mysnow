import { fetchWeatherData } from './src/api';
import { blendForecasts, groupData } from './src/data';
import { getSLRCategory } from './src/data';

async function test() {
    const response = await fetch("https://api.open-meteo.com/v1/forecast?latitude=39.193416&longitude=-120.245402&hourly=snowfall,precipitation,temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m,snow_depth,precipitation_probability,apparent_temperature,relative_humidity_2m,wind_gusts_10m,cloud_cover,freezing_level_height,weather_code,wet_bulb_temperature_2m,pressure_msl,temperature_850hPa,temperature_700hPa,soil_temperature_0cm,snowfall_water_equivalent&models=best_match");
    const ecmwfData = await response.json();
    const location = { elevationM: 2000 };
    
    // Simulate what blendForecasts does directly:
    const blendedData = blendForecasts(null, ecmwfData, location, 'sierra_custom');
    const daysData = groupData(blendedData).slice(0, 14);
    
    const day = daysData[0];
    const h = day.hourly[0];
    
    console.log("h.snowLevel:", h.snowLevel, typeof h.snowLevel);
    console.log("Rendered string:", h.snowLevel !== null ? h.snowLevel.toFixed(0) + 'm' : '--');
}
test().catch(console.error);
