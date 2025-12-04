# Guidearr - Dispatcharr Channel Guide

A beautiful, web-based TV channel guide generator that pulls data from your Dispatcharr instance and creates customizable, printable channel guides.

## Features

- 🎨 **Dark/Light Mode**: Toggle between dark and light themes with persistent preference
- 📄 **Dual Print Modes**: Choose between detailed (all channels) or summary (channel ranges) per group
- 🖨️ **Auto-Print Dialog**: Automatically opens print dialog when generating printable guides
- 🔄 **Smart Caching**: Configurable refresh schedule to minimize API calls
- 🎯 **Flexible Filtering**: Select which channel groups to include and exclude
- 🌈 **Continuation Headers**: Automatic headers when groups span across columns
- 🏃 **Multi-Architecture**: Supports both AMD64 and ARM64 platforms
- 🔧 **Easy Configuration**: All settings via environment variables
- 📱 **Responsive Interface**: Beautiful web interface for viewing and selecting groups

## Quick Start

### Using Docker Run (Simplest)

```bash
docker run -d \
  --name guidearr \
  -p 9198:5000 \
  -e DISPATCHARR_BASE_URL=http://your-dispatcharr:9191 \
  -e DISPATCHARR_USERNAME=your-username \
  -e DISPATCHARR_PASSWORD=your-password \
  ghcr.io/motwakorb/guidearr:latest
```

Then access the channel guide at `http://localhost:9198`

### Using Docker Compose (Recommended)

1. **Create a `docker-compose.yml` file**:

```yaml
version: '3.8'

services:
  guidearr:
    image: ghcr.io/motwakorb/guidearr:latest
    container_name: guidearr
    restart: unless-stopped
    ports:
      - "9198:5000"
    environment:
      # Required: Dispatcharr API Configuration
      - DISPATCHARR_BASE_URL=http://your-dispatcharr:9191
      - DISPATCHARR_USERNAME=your-username
      - DISPATCHARR_PASSWORD=your-password

      # Optional: Filter to specific channel profile
      - CHANNEL_PROFILE_NAME=

      # Optional: Exclude channel groups (comma-separated)
      - EXCLUDE_CHANNEL_GROUPS=

      # Optional: Customize page title
      - PAGE_TITLE=TV Channel Guide

      # Optional: Cache refresh schedule (cron format)
      - CACHE_REFRESH_CRON=0 */6 * * *

    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

2. **Start the container**:

```bash
docker-compose up -d
```

3. **Access the channel guide** at `http://localhost:9198`

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISPATCHARR_BASE_URL` | Yes | `http://localhost:9191` | Base URL of your Dispatcharr instance |
| `DISPATCHARR_USERNAME` | Yes | - | Dispatcharr username |
| `DISPATCHARR_PASSWORD` | Yes | - | Dispatcharr password |
| `CHANNEL_PROFILE_NAME` | No | - | Filter to channels in a specific Channel Profile |
| `EXCLUDE_CHANNEL_GROUPS` | No | - | Comma-separated list of channel groups to exclude |
| `PAGE_TITLE` | No | `TV Channel Guide` | Custom title for the HTML page |
| `CACHE_REFRESH_CRON` | No | `0 */6 * * *` | Cron expression for cache refresh schedule (minute hour day month day_of_week) |

### Configuration Examples

**Filter to "Family Friendly" profile:**
```yaml
- CHANNEL_PROFILE_NAME=Family Friendly
```

**Exclude specific channel groups:**
```yaml
- EXCLUDE_CHANNEL_GROUPS=Adult Content,Premium Sports
```

**Custom page title:**
```yaml
- PAGE_TITLE=Smith Family TV Channels
```

**Custom cache refresh schedule:**
```yaml
# Refresh every 4 hours
- CACHE_REFRESH_CRON=0 */4 * * *

# Refresh daily at 3 AM
- CACHE_REFRESH_CRON=0 3 * * *

# Refresh every 30 minutes
- CACHE_REFRESH_CRON=*/30 * * * *
```

## Using the Application

### Main Guide Interface

- **View Channels**: Browse all your channels organized by groups
- **Theme Toggle**: Click the "🌙 Dark / ☀️ Light" button in the top-right to switch themes
- **Printable Guide**: Click "📄 Printable Guide" to select which groups to print

### Creating Printable Guides

1. Click the "📄 Printable Guide" button
2. Select which channel groups to include
3. Choose "Detailed" (all channels) or "Summary" (channel range) for each group
4. Click "Print Selected"
5. The print dialog will automatically open in a new window
6. Print or save as PDF

### Available Endpoints

- **Main page**: `http://localhost:9198`
- **Health check**: `http://localhost:9198/health`
- **Manual refresh**: `http://localhost:9198/refresh` (forces immediate cache refresh)

## How Caching Works

The application uses smart caching to provide instant page loads:

1. **Startup**: When the container starts, it immediately fetches all data from Dispatcharr (channels, groups, logos) and generates the HTML
2. **Fast Serving**: All subsequent page loads are served instantly from the cache
3. **Scheduled Refresh**: The cache automatically refreshes based on your `CACHE_REFRESH_CRON` setting (default: every 6 hours)
4. **Manual Refresh**: Visit `/refresh` to force an immediate cache update

The health endpoint (`/health`) shows:
- Whether the cache is populated
- When it was last updated
- Any errors that occurred during refresh

## Updating the Container

```bash
# Pull the latest version
docker pull ghcr.io/motwakorb/guidearr:latest

# If using docker-compose
docker-compose pull
docker-compose up -d

# If using docker run, stop and remove the old container first
docker stop guidearr
docker rm guidearr
# Then run the docker run command again with the latest image
```

## Troubleshooting

### Container Won't Start

Check the logs:
```bash
docker logs guidearr
```

### Authentication Errors

- Verify `DISPATCHARR_USERNAME` and `DISPATCHARR_PASSWORD` are correct
- Ensure `DISPATCHARR_BASE_URL` is reachable from the container
- If Dispatcharr is on the same host, use `http://host.docker.internal:9191` (on Mac/Windows) or the host's IP address (on Linux)

### No Channels Showing

- Check that your Dispatcharr instance has channels configured
- Verify network connectivity between the container and Dispatcharr
- Review container logs for error messages
- Visit `/health` endpoint to check cache status

### Using with Nginx Reverse Proxy

If you want to expose the guide through nginx:

```nginx
server {
    listen 80;
    server_name channels.yourdomain.com;

    location / {
        proxy_pass http://localhost:9198;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Advanced Configuration

### Multiple Channel Guides

You can run multiple instances for different configurations:

```yaml
version: '3.8'

services:
  kids-channels:
    image: ghcr.io/motwakorb/guidearr:latest
    ports:
      - "9198:5000"
    environment:
      - DISPATCHARR_BASE_URL=http://dispatcharr:9191
      - DISPATCHARR_USERNAME=admin
      - DISPATCHARR_PASSWORD=password
      - CHANNEL_PROFILE_NAME=Kids Safe
      - PAGE_TITLE=Kids Channels

  sports-channels:
    image: ghcr.io/motwakorb/guidearr:latest
    ports:
      - "9199:5000"
    environment:
      - DISPATCHARR_BASE_URL=http://dispatcharr:9191
      - DISPATCHARR_USERNAME=admin
      - DISPATCHARR_PASSWORD=password
      - CHANNEL_PROFILE_NAME=Sports
      - PAGE_TITLE=Sports Channels
```

### Different Port

If you're running on a different port (e.g., 8080), change the port mapping:
```yaml
ports:
  - "8080:5000"
```

Then access at `http://localhost:8080`

## Security Notes

- Use strong passwords for your Dispatcharr account
- Consider using Docker secrets for production deployments
- Run behind HTTPS in production (use nginx with Let's Encrypt)
- The container runs as a non-root user for improved security

## Support

For issues specific to:
- **Dispatcharr API**: Check Dispatcharr documentation
- **Application errors**: Check container logs with `docker logs guidearr`
- **Container builds**: Check [GitHub Actions](https://github.com/MotWakorb/guidearr/actions)

## License

This project is provided as-is for use with Dispatcharr instances.
