## What's New in v1.1.0

### 🎉 Major Features

**EPG Data Support**
- Fixed missing `timedelta` import that prevented EPG data from loading
- EPG data now displays correctly showing current and upcoming programs
- All channels with EPG data now show "Now Playing" and "Up Next" information

**Refresh Button**
- Added refresh button to both List View and Grid View
- Located in the top header bar for easy access
- Shows loading state and confirmation when cache is refreshed
- Automatically reloads the page with updated data

**Channel Range Override**
- New feature in printable guide modal
- Allows manual entry of channel ranges for groups with non-contiguous channels
- Automatically calculates channel count from specified range
- Example: Enter "200-220" to show 21 channels instead of actual count

### 🎨 UI/UX Improvements

**Button Layout**
- Moved Theme Toggle and Refresh buttons to header bar
- No longer obscures grid view content
- Consistent placement across all views

**Button Styling**
- Updated all buttons to consistent rectangular styling (6px border-radius)
- Removed mixed rounded/rectangular button styles
- Cleaner, more professional appearance

### 🐛 Bug Fixes

- Fixed EPG data not displaying due to missing Python import
- Resolved floating button overlap issues in grid view

### 📦 Deployment

**Pre-built images available:**
```bash
docker pull ghcr.io/motwakorb/guidearr:latest
docker pull ghcr.io/motwakorb/guidearr:v1.1.0
docker pull ghcr.io/motwakorb/guidearr:1.1
```

**Update existing installation:**
```bash
docker compose pull
docker compose up -d
```

### 🏗️ Technical Details

- Multi-architecture support: `linux/amd64` and `linux/arm64`
- Improved caching with proper timezone handling
- Enhanced modal UI with flexible layout for new features

---

**Full Changelog**: https://github.com/MotWakorb/guidearr/compare/v1.0.0...v1.1.0
