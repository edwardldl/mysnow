"use client";

import React, { useState, useEffect, useRef } from "react";
import { Search, MapPin, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils_tailwind";
import skiAreasData from "@/data/ski-areas.json";
import { Location } from "@/lib/types";

interface SkiArea {
  name: string;
  lat: string;
  lon: string;
  region: string;
  country: string;
}

interface LocationSelectorProps {
  locations: Record<string, Location>;
  currentLocationId: string;
  onSelect: (id: string) => void;
  onAdd: (name: string, lat: string, lon: string) => void;
  onRemove: (id: string) => void;
}

export default function LocationSelector({
  locations,
  currentLocationId,
  onSelect,
  onAdd,
  onRemove,
}: LocationSelectorProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SkiArea[]>([]);
  const [coords, setCoords] = useState("");
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  
  const searchRef = useRef<HTMLDivElement>(null);

  const handleSearch = (query: string) => {
    setSearchQuery(query);
    if (query.length < 2) {
      setSearchResults([]);
      return;
    }
    const matches = (skiAreasData as SkiArea[])
      .filter((area) => 
        area.name.toLowerCase().includes(query.toLowerCase())
      )
      .slice(0, 8);
    setSearchResults(matches);
  };

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) {
        setIsSearchOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSelectArea = (area: SkiArea) => {
    setSearchQuery(area.name);
    setCoords(`${area.lat}, ${area.lon}`);
    setIsSearchOpen(false);
  };

  const handleAdd = () => {
    const [lat, lon] = coords.split(/[\s,]+/).filter(Boolean);
    if (lat && lon) {
      onAdd(searchQuery || `${parseFloat(lat).toFixed(2)}, ${parseFloat(lon).toFixed(2)}`, lat, lon);
      setSearchQuery("");
      setCoords("");
    }
  };

  return (
    <div className="flex flex-col gap-6 w-full max-w-7xl mx-auto px-4 md:px-8 mt-8">
      {/* Location Switcher Chips */}
      <div className="flex flex-wrap gap-2">
        {Object.values(locations).map((loc) => (
          <div
            key={loc.id}
            className={cn(
              "group flex items-center gap-2 px-4 py-2 rounded-full cursor-pointer transition-all duration-300 border",
              loc.id === currentLocationId
                ? "bg-accent-blue/20 border-accent-blue text-accent-cyan shadow-lg shadow-blue-500/10"
                : "bg-white/5 border-white/10 text-slate-400 hover:bg-white/10 hover:border-white/20 hover:text-slate-200"
            )}
            onClick={() => onSelect(loc.id)}
          >
            <MapPin className={cn("w-4 h-4", loc.id === currentLocationId ? "text-accent-cyan" : "text-slate-500")} />
            <span className="text-sm font-medium">{loc.name}</span>
            {loc.isCustom && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(loc.id);
                }}
                className="opacity-0 group-hover:opacity-100 p-0.5 hover:bg-white/10 rounded-full transition-opacity"
              >
                <X className="w-3 h-3 text-slate-400 hover:text-accent-rose" />
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Search & Add Form */}
      <div className="grid grid-cols-1 md:grid-cols-12 gap-3 items-center">
        <div className="md:col-span-5 relative" ref={searchRef}>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              type="text"
              placeholder="Search ski area (e.g. Whistler, Vail)"
              value={searchQuery}
              onChange={(e) => {
                handleSearch(e.target.value);
                setIsSearchOpen(true);
              }}
              onFocus={() => setIsSearchOpen(true)}
              className="w-full bg-white/5 border border-white/10 rounded-xl py-3 pl-10 pr-4 text-sm text-white focus:outline-none focus:border-accent-blue/50 focus:bg-white/[0.07] transition-all"
            />
          </div>
          
          {isSearchOpen && searchResults.length > 0 && (
            <div className="absolute top-full left-0 w-full mt-2 glass-panel p-2 rounded-xl border border-white/10 shadow-2xl z-[100] max-h-64 overflow-y-auto no-scrollbar">
              {searchResults.map((area, i) => (
                <div
                  key={i}
                  onClick={() => handleSelectArea(area)}
                  className="flex flex-col p-3 rounded-lg hover:bg-white/10 cursor-pointer transition-colors"
                >
                  <span className="text-sm font-semibold text-white">{area.name}</span>
                  <span className="text-[10px] text-slate-400 uppercase tracking-wider">
                    {area.region}, {area.country}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="md:col-span-5 relative">
          <input
            type="text"
            placeholder="Latitude, Longitude"
            value={coords}
            onChange={(e) => setCoords(e.target.value)}
            className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 text-sm text-white focus:outline-none focus:border-accent-blue/50 focus:bg-white/[0.07] transition-all"
          />
        </div>

        <button
          onClick={handleAdd}
          className="md:col-span-2 h-[46px] bg-accent-blue hover:bg-blue-500 text-white rounded-xl shadow-lg shadow-blue-500/20 transition-all font-bold text-sm flex items-center justify-center gap-2"
        >
          <Plus className="w-5 h-5" />
          Add Area
        </button>
      </div>
    </div>
  );
}
