import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Read the CSV file
const csvPath = path.join(__dirname, 'src/assets/ski_areas.csv');
const csvContent = fs.readFileSync(csvPath, 'utf8');

// Parse CSV
const lines = csvContent.split('\n').filter(line => line.trim());
const headers = lines[0].split(',');

// Generate TypeScript array
const skiAreas = lines.slice(1).map(line => {
    const [name, country, region, lat, lon] = line.split(',');
    return {
        name: name.replace(/"/g, '\\"'),
        country: country.replace(/"/g, '\\"'),
        region: region.replace(/"/g, '\\"'),
        lat: lat,
        lon: lon
    };
});

// Generate JSON for public folder
const jsonPath = path.join(__dirname, 'public/ski-areas.json');
fs.writeFileSync(jsonPath, JSON.stringify(skiAreas, null, 2), 'utf8');

console.log('Generated public/ski-areas.json with', skiAreas.length, 'ski areas');