export interface OpenMeteoHourly {
    time: string[];
    temperature_2m: number[];
    dew_point_2m?: number[];
    wind_speed_10m?: number[];
    wind_direction_10m?: number[];
    snow_depth?: number[];
    precipitation_probability?: number[];
    apparent_temperature?: number[];
    relative_humidity_2m?: number[];
    wind_gusts_10m?: number[];
    cloud_cover?: number[];
    freezing_level_height?: number[];
    weather_code?: number[];
    wet_bulb_temperature_2m?: number[];
    specific_humidity_2m?: number[];
    pressure_msl?: number[];
    temperature_850hPa?: number[];
    temperature_700hPa?: number[];
    soil_temperature_0cm?: number[];
    precipitation?: number[];
    snowfall?: number[];
    snowfall_water_equivalent?: number[];
    [key: string]: any; // Allows dynamic pressure level keys
}

export interface OpenMeteoDaily {
    time: string[];
    sunrise?: string[];
    sunset?: string[];
}

export interface OpenMeteoResponse {
    hourly: OpenMeteoHourly;
    daily?: OpenMeteoDaily;
    elevation?: number;
}

export interface Location {
    id: string;
    name: string;
    latitude: number;
    longitude: number;
    elevationM: number;
    elevationFt: number;
    isCustom?: boolean;
}

export interface BlendedHour {
    time: string;
    dateObj: Date;
    model: string;
    precipitation: number;
    liquidMM: number;
    temperature: number;
    dewPoint: number | null;
    windSpeed: number | null;
    windDir: number | null;
    snowDepth: number | null;
    precipChance: number | null;
    feelsLike: number | null;
    rh: number | null;
    gusts: number | null;
    clouds: number | null;
    snowLevel: number | null;
    weatherCode: number | null;
    wet_bulb_temperature_2m?: number | null;
    specific_humidity_2m?: number | null;
    pressure_msl?: number | null;
    temperature_850hPa?: number | null;
    temperature_700hPa?: number | null;
    soil_temperature_0cm?: number | null;
    snowfall_raw?: number | null;
    slr: number | null;
    snowfall: number;
    method: string | null;
    slrCategory: string | null;
    layers?: any[];
}

export interface WindowData {
    startHour: number;
    label: string;
    snowfall: number;
    precip: number;
    temps: number[];
    windSpeeds: number[];
    windDirs: number[];
    slrs: number[];
    precipChances: number[];
    feelsLikes: number[];
    rhs: number[];
    gusts: number[];
    clouds: number[];
    snowLevels: number[];
    avgTemp?: number | null;
    avgWindSpeed?: number | null;
    dominantWindDir?: number | null;
    avgSlr?: number | null;
    slrCategory?: string | null;
    maxPrecipChance?: number | null;
    avgFeelsLike?: number | null;
    avgRh?: number | null;
    maxGust?: number | null;
    avgCloud?: number | null;
    avgSnowLevel?: number | null;
}

export interface DayData {
    dateStr: string;
    dateObj: Date;
    sunrise: string | null;
    sunset: string | null;
    totalSnowfall: number;
    totalPrecipitation: number;
    models: Set<string>;
    modelString?: string;
    hourly: BlendedHour[];
    windows: WindowData[];
    snowDepthValues: number[];
    snowDepth?: string;
    snowLayersOnGround: Array<{ SWE_mm: number; density: number; ageInHours: number }>;
    maxHourlySnowfall?: number;
    maxWindowSnowfall?: number;
}
