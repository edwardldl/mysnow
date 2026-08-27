"use client";

import React from "react";
import Link from "next/link";
import { Info, Map as MapIcon, Database, Mountain, FlaskConical, ArrowRight } from "lucide-react";
import ModeToggle from "./ModeToggle";

interface CreditsFooterProps {
  mode: "forecast" | "history";
  setMode: (mode: "forecast" | "history") => void;
}

export default function CreditsFooter({ mode, setMode }: CreditsFooterProps) {
  return (
    <footer className="w-full bg-slate-950 border-t border-white/5 py-12 px-4 md:px-8 mt-20">
      <div className="max-w-7xl mx-auto mb-12 flex justify-center">
        <ModeToggle mode={mode} setMode={setMode} />
      </div>
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

      <div className="max-w-7xl mx-auto mt-10">
        <Link
          href="/research/slr"
          className="group flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-2xl border border-cyan-300/10 bg-cyan-300/[0.035] px-5 py-4 transition-all hover:border-cyan-300/25 hover:bg-cyan-300/[0.06] focus:outline-none focus:ring-2 focus:ring-accent-cyan/50"
        >
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-cyan-400/10 text-cyan-200">
              <FlaskConical className="h-4 w-4" />
            </span>
            <div>
              <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300/70">Model research</p>
              <p className="mt-1 text-xs font-bold text-white">Explore the regional SLR comparison</p>
            </div>
          </div>
          <span className="inline-flex items-center gap-2 text-[10px] font-bold text-cyan-200 transition group-hover:text-white">
            Open evaluation <ArrowRight className="h-3.5 w-3.5 transition group-hover:translate-x-0.5" />
          </span>
        </Link>
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
