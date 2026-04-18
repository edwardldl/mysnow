import fs from 'fs';
import path from 'path';

const csvPath = 'legacy/ski_areas.csv';
const jsonPath = 'src/data/ski-areas.json';

function parseCSV(content) {
  const lines = content.split(/\r?\n/);
  if (lines.length === 0) return [];

  const headers = parseLine(lines[0]);
  const result = [];

  for (let i = 1; i < lines.length; i++) {
    if (!lines[i].trim()) continue;
    const values = parseLine(lines[i]);
    const entry = {};
    headers.forEach((header, index) => {
      entry[header] = values[index];
    });
    result.push(entry);
  }

  return result;
}

function parseLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === ',' && !inQuotes) {
      result.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }
  result.push(current.trim());
  return result;
}

try {
  const csvContent = fs.readFileSync(csvPath, 'utf8');
  const rawData = parseCSV(csvContent);

  const mappedData = rawData.map(item => ({
    name: item.name || '',
    country: item.countries || '',
    region: item.regions || '',
    lat: item.lat || '',
    lon: item.lng || '', // Mapping lng to lon
    min_elevation_m: item.min_elevation_m ? parseInt(item.min_elevation_m, 10) : null,
    max_elevation_m: item.max_elevation_m ? parseInt(item.max_elevation_m, 10) : null
  }));

  fs.mkdirSync(path.dirname(jsonPath), { recursive: true });
  fs.writeFileSync(jsonPath, JSON.stringify(mappedData, null, 2));

  console.log(`Successfully converted ${mappedData.length} ski areas to ${jsonPath}`);
} catch (error) {
  console.error('Error converting CSV to JSON:', error);
  process.exit(1);
}
