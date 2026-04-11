"use client";

import React from "react";
import { Info, Map as MapIcon, Database, Mountain } from "lucide-react";

export default function CreditsFooter() {
  return (
    <footer className="w-full bg-slate-950 border-t border-white/5 py-12 px-4 md:px-8 mt-20">
      <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-10">
        
        {/* Weather Data */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2 text-white">
            <Database className="w-4 h-4 text-accent-cyan" />
            <span className="text-xs font-black uppercase tracking-widest">Weather Data</span>
          </div>
          <p className="text-[10px] leading-relaxed text-slate-500 font-medium">
            Forecast data provided by <a href="https://open-meteo.com/" target="_blank" rel="noreferrer" className="text-accent-cyan hover:underline">Open-Meteo.com</a>. Licensed under CC BY 4.0. Multiple ensembles including HRRR, GFS, and ECMWF are utilized.
          </p>
        </div>

        {/* Map Data */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2 text-white">
            <MapIcon className="w-4 h-4 text-blue-400" />
            <span className="text-xs font-black uppercase tracking-widest">Cartography</span>
          </div>
          <p className="text-[10px] leading-relaxed text-slate-500 font-medium">
            © <a href="https://www.openstreetmap.org/copyright" className="hover:text-white transition-colors">OpenStreetMap</a> contributors and <a href="https://openskimap.org/" className="hover:text-white transition-colors">OpenSkiMap.org</a>. Vector tiles provided by OpenFreeMap.
          </p>
        </div>

        {/* Terrain Data */}
        <div className="flex flex-col gap-3 lg:col-span-2">
          <div className="flex items-center gap-2 text-white">
            <Mountain className="w-4 h-4 text-rose-400" />
            <span className="text-xs font-black uppercase tracking-widest">Terrain & Elevation</span>
          </div>
          <div className="flex flex-col gap-2">
            <p className="text-[10px] leading-relaxed text-slate-500 font-medium">
              High-resolution elevation data compilation by TechIdiots.net. Sources: JAXA AW3D30 (Global), Sonny DTM (Europe), IGN RGE Alti (France), SwissAlti3D, and various regional providers.
            </p>
            <p className="text-[9px] text-slate-600 italic">
              Special thanks to the open-science community for high-resolution DEM datasets.
            </p>
          </div>
        </div>

      </div>

      <div className="max-w-7xl mx-auto mt-12 pt-8 border-t border-white/[0.02] flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2 opacity-30 grayscale">
          <Info className="w-4 h-4" />
          <span className="text-[10px] font-black uppercase tracking-widest text-white">MySnow v4.0 (Redesign)</span>
        </div>
        <p className="text-[10px] text-slate-600 font-bold">
          Built for the backcountry. Use at your own risk.
        </p>
      </div>
    </footer>
  );
}
