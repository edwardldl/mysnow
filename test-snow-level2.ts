import { blendForecasts } from './src/data';
import { calcSLR } from './src/slr';

async function test() {
    const response = await fetch("https://api.open-meteo.com/v1/forecast?latitude=39.193416&longitude=-120.245402&hourly=snowfall,precipitation,temperature_2m,dew_point_2m,wind_speed_10m,wind_direction_10m,snow_depth,precipitation_probability,apparent_temperature,relative_humidity_2m,wind_gusts_10m,cloud_cover,freezing_level_height,weather_code,wet_bulb_temperature_2m,pressure_msl,temperature_850hPa,temperature_700hPa,soil_temperature_0cm,snowfall_water_equivalent&models=best_match");
    const ecmwfData = await response.json();
    const location = { elevationM: 2000 };
    
    // Simulate what blendForecasts does directly:
    const blendedData = blendForecasts(null, ecmwfData, location, 'sierra_custom');
    const firstPoint = blendedData.hourly[0];
    
    console.log("First point RAW freezing level:", ecmwfData.hourly.freezing_level_height[0]);
    console.log("First point output snowLevel:", firstPoint.snowLevel);
}
test().catch(console.error);
