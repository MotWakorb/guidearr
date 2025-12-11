## What's New in v1.2.0

### 🎉 Major Features

**Live Progress Bars**
- Added real-time progress bars showing program completion status
- Bars automatically update every second without page refresh
- Visual indicator of how far through the current program you are
- Theme-aware colors (lighter in dark mode, darker in light mode)

**Sticky Headers**
- Main header now stays visible when scrolling
- Channel group titles stick below main header during scroll
- Improved navigation and context while browsing channels
- Z-index optimized to prevent visual stacking issues

**Grid View Enhancements**
- Added hover tooltips to show full channel names when truncated
- Improved readability for long channel names

### 🎨 UI/UX Improvements

**Button Reorganization**
- Moved Grid View and Printable Guide buttons from floating to header
- All action buttons now consistently placed in header bar
- Cleaner interface with no floating elements

**Layout Optimization**
- Removed rounded corners from headers for cleaner edges
- Optimized channel name column width (300px)
- Reduced row height for more compact, efficient layout
- Added text overflow handling with ellipsis for long names

**Visual Polish**
- Progress bars with smooth gradient fills
- Consistent button styling across all views
- Better spacing and alignment throughout

### 🐛 Bug Fixes

- Fixed EPG data not loading due to missing `timedelta` import in `refresh_cache()` function
- Resolved header positioning and z-index conflicts
- Fixed content visibility through rounded corners during scroll

### 📦 Deployment

**Pre-built images available:**
```bash
docker pull ghcr.io/motwakorb/guidearr:latest
docker pull ghcr.io/motwakorb/guidearr:v1.2.0
docker pull ghcr.io/motwakorb/guidearr:1.2
```

**Update existing installation:**
```bash
docker compose pull
docker compose up -d
```

### 🏗️ Technical Details

- Multi-architecture support: `linux/amd64` and `linux/arm64`
- JavaScript-based live updates (1s interval) for progress bars
- CSS sticky positioning with proper z-index layering
- Server-side progress calculation with client-side animation
- Theme-specific styling using CSS variables

---

**Full Changelog**: https://github.com/MotWakorb/guidearr/compare/v1.1.0...v1.2.0
