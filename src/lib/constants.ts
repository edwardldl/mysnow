export interface ConfigOption {
  id: string;
  name: string;
  desc: string;
  badge?: string;
}

export const MODELS: ConfigOption[] = [
  { id: "best_match", name: "Best Match (Ensemble)", desc: "AI selects the best local models. 7-15 days.", badge: "7-15D" },
  { id: "hrrr_ecmwf", name: "Blended (HRRR+ECMWF)", desc: "HRRR (3km) + ECMWF High-Res (9km). Optimal high-res blend.", badge: "0-15D" },
  { id: "hrrr", name: "NCEP HRRR", desc: "3km / USA | 0-48h. Gold standard for US mountains.", badge: "0-48H" },
  { id: "gem_hrdps_west", name: "GEM HRDPS West", desc: "2.5km / Canada | 0-48h. Excellent for deep Western valleys.", badge: "0-48H" },
  { id: "nbm", name: "NCEP NBM", desc: "2.5km / USA | 0-7d. Calibrated consensus blend.", badge: "0-7D" },
  { id: "nam", name: "NCEP NAM", desc: "3km / USA | 0-84h. Precise mesoscale tracking.", badge: "0-84H" },
  { id: "gem_regional", name: "GEM Regional", desc: "10km / N. Am | 1-3d. Reliable coastal storm bridge.", badge: "1-3D" },
  { id: "ecmwf", name: "ECMWF IFS Global", desc: "25km / Global | 3-10d. Standard global consensus model.", badge: "3-10D" },
  { id: "gfs", name: "NCEP GFS", desc: "~13km / Global | 3-14+ d. Standard US global.", badge: "3-14D" },
];

export const ALGORITHMS: ConfigOption[] = [
  { id: "hybrid", name: "Hybrid (Kuchera-Cobb)", desc: "Physical depth + wind compaction boost." },
  { id: "cobb", name: "Cobb (DGZ-Enhanced)", desc: "Piecewise curve + saturated DGZ lift." },
  { id: "dendro", name: "Dendro (Habit Diagram)", desc: "Physics-based crystal habit & DGZ depth." },
  { id: "krc", name: "KRC-Comp (High-Fi)", desc: "Riming + Wind Shear fragmentation logic." },
  { id: "kuchera_plus", name: "Kuchera (DGZ-Plus)", desc: "Vanilla + true physical DGZ depth boost." },
  { id: "simple", name: "Kuchera (Vanilla)", desc: "Piecewise regression on max temp aloft." },
  { id: "standard", name: "10:1 (Fixed)", desc: "Constant ratio: 1cm snow per 1mm liquid." },
];
