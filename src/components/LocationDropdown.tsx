"use client";

import React, { useState, useEffect, useRef } from "react";
import { ChevronDown, MapPin, Plus, X, Search, Check } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils_tailwind";
import skiAreasData from "@/data/ski-areas.json";
import { Location } from "@/lib/types";
import { isValidCoordinate } from "@/lib/api";

interface SkiArea {
  name: string;
  lat: string;
  lon: string;
  region: string;
  country: string;
  minElevationM?: number;
  maxElevationM?: number;
}

interface LocationDropdownProps {
  locations: Record<string, Location>;
  currentLocationId: string;
  onSelect: (id: string) => void;
  onAdd: (name: string, lat: string, lon: string, minElev?: number, maxElev?: number) => void;
  onRemove: (id: string) => void;
}

export default function LocationDropdown({
  locations,
  currentLocationId,
  onSelect,
  onAdd,
  onRemove,
}: LocationDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [view, setView] = useState<"list" | "add">("list");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SkiArea[]>([]);
  const [coords, setCoords] = useState("");
  const [selectedArea, setSelectedArea] = useState<SkiArea | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const currentLocation = locations[currentLocationId] || Object.values(locations)[0];

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        // Reset view after a delay to allow exit animation to finish or just immediately
        setTimeout(() => setView("list"), 200);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Clear error when inputs change
  useEffect(() => {
    setError(null);
  }, [searchQuery, coords]);

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
      .slice(0, 5);
    setSearchResults(matches);
  };

  const handleSelectArea = (area: SkiArea) => {
    setSearchQuery(area.name);
    setCoords(`${area.lat}, ${area.lon}`);
    setSelectedArea(area);
    setSearchResults([]);
  };

  const handleAdd = () => {
    setError(null);
    const [latStr, lonStr] = coords.split(/[\s,]+/).filter(Boolean);
    
    if (!latStr || !lonStr) {
      setError("Please enter both latitude and longitude.");
      return;
    }

    const lat = parseFloat(latStr);
    const lon = parseFloat(lonStr);

    if (!isValidCoordinate(lat, lon)) {
      setError("Invalid coordinates. Latitude must be -90 to 90, Longitude -180 to 180.");
      return;
    }

    try {
      const minElev = selectedArea?.minElevationM;
      const maxElev = selectedArea?.maxElevationM;

      onAdd(searchQuery || `${lat.toFixed(2)}, ${lon.toFixed(2)}`, latStr, lonStr, minElev, maxElev);
      setSearchQuery("");
      setCoords("");
      setSelectedArea(null);
      setView("list");
      setIsOpen(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add location");
    }
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 px-2 h-9 sm:px-3 md:px-4 md:h-11 rounded-full bg-white/5 border border-white/10 hover:bg-white/10 hover:border-white/20 transition-all group"
      >
        <MapPin className="w-3.5 h-3.5 md:w-4 md:h-4 text-accent-cyan" />
        <span className="text-xs md:text-sm font-bold text-white max-w-[65px] sm:max-w-[120px] lg:max-w-[200px] truncate">
          {currentLocation?.name}
        </span>
        <ChevronDown className={cn("w-3.5 h-3.5 md:w-4 md:h-4 text-slate-400 transition-transform duration-300", isOpen && "rotate-180")} />
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.95 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="fixed md:absolute top-[calc(60px+var(--sat,0px)+8px)] md:top-full left-4 right-4 md:left-auto md:right-0 md:ml-0 md:w-80 bg-slate-950/98 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl z-[100]"
          >
            {view === "list" ? (
              <div className="p-2">
                <div className="max-h-64 overflow-y-auto no-scrollbar py-1">
                  {Object.values(locations).map((loc) => (
                    <div
                      key={loc.id}
                      className={cn(
                        "group flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl cursor-pointer transition-all",
                        loc.id === currentLocationId
                          ? "bg-accent-blue/10 text-accent-cyan"
                          : "hover:bg-white/5 text-slate-300 hover:text-white"
                      )}
                      onClick={() => {
                        onSelect(loc.id);
                        setIsOpen(false);
                      }}
                    >
                      <div className="flex items-center gap-3 truncate">
                        <MapPin className={cn("w-4 h-4 shrink-0", loc.id === currentLocationId ? "text-accent-cyan" : "text-slate-500")} />
                        <span className="text-sm font-medium truncate">{loc.name}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {loc.id === currentLocationId && <Check className="w-4 h-4 md:opacity-100" />}
                        {loc.isCustom && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onRemove(loc.id);
                            }}
                            className={cn(
                              "p-1 hover:bg-white/10 rounded-full text-slate-500 hover:text-accent-rose transition-colors",
                              loc.id === currentLocationId ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                            )}
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                
                <div className="mt-1 pt-1 border-t border-white/5">
                  <button
                    onClick={() => setView("add")}
                    className="w-full flex items-center gap-3 px-3 py-3 rounded-xl hover:bg-white/10 text-slate-400 hover:text-white transition-all text-sm font-semibold"
                  >
                    <div className="p-1 bg-white/5 rounded-lg border border-white/10">
                      <Plus className="w-4 h-4" />
                    </div>
                    Add New Location
                  </button>
                </div>
              </div>
            ) : (
              <div className="p-4 space-y-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-bold text-white">Add New Location</h3>
                  <button onClick={() => setView("list")} className="p-1 hover:bg-white/10 rounded-full transition-colors">
                    <X className="w-4 h-4 text-slate-500" />
                  </button>
                </div>
                
                <div className="space-y-3">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                    <input
                      autoFocus
                      type="text"
                      placeholder="Search ski area..."
                      value={searchQuery}
                      onChange={(e) => handleSearch(e.target.value)}
                      className="w-full bg-white/5 border border-white/10 rounded-xl py-2.5 pl-10 pr-4 text-sm text-white focus:outline-none focus:border-accent-blue/50 transition-all font-sans"
                    />
                    
                    {searchResults.length > 0 && (
                      <div className="absolute top-full left-0 w-full mt-2 bg-slate-900 border border-white/20 rounded-xl shadow-2xl z-[110] max-h-60 overflow-y-auto no-scrollbar">
                        {searchResults.map((area, i) => (
                          <div
                            key={i}
                            onClick={() => handleSelectArea(area)}
                            className="flex flex-col p-2.5 rounded-lg hover:bg-white/10 cursor-pointer transition-colors"
                          >
                            <span className="text-xs font-semibold text-white">{area.name}</span>
                            <span className="text-[10px] text-slate-400 uppercase tracking-wider">
                              {area.region}, {area.country}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <input
                    type="text"
                    placeholder="Latitude, Longitude"
                    value={coords}
                    onChange={(e) => setCoords(e.target.value)}
                    className={cn(
                        "w-full bg-white/5 border rounded-xl py-2.5 px-4 text-sm text-white focus:outline-none transition-all font-sans",
                        error ? "border-accent-rose/50 bg-accent-rose/5" : "border-white/10 focus:border-accent-blue/50"
                    )}
                  />

                  {error && (
                    <motion.p 
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="text-[10px] font-bold text-accent-rose px-1"
                    >
                        {error}
                    </motion.p>
                  )}

                  <div className="flex gap-2 pt-2">
                    <button
                      onClick={() => setView("list")}
                      className="flex-1 py-2.5 rounded-xl border border-white/10 text-slate-400 hover:text-white hover:bg-white/5 transition-all text-xs font-bold"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleAdd}
                      disabled={!coords}
                      className="flex-1 py-2.5 bg-accent-blue hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl shadow-lg shadow-blue-500/20 transition-all font-bold text-xs"
                    >
                      Add Area
                    </button>
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
