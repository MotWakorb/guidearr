# Guidearr - Dispatcharr Channel Guide

A beautiful, web-based TV channel guide generator that pulls data from your Dispatcharr instance and creates customizable, printable channel guides.

## Features

- 🎨 **Colorful Design**: Each channel group gets its own color scheme with matching light backgrounds
- 📄 **Dual Print Modes**: Choose between detailed (all channels) or summary (channel ranges) per group
- 🔄 **Smart Caching**: Configurable refresh schedule to minimize API calls
- 🖨️ **Print-Optimized**: Generates clean, professional guides optimized for 8.5" x 11" landscape
- 🎯 **Flexible Filtering**: Select which channel groups to include and exclude
- 🌈 **Continuation Headers**: Automatic headers when groups span across columns
- 🏃 **Multi-Architecture**: Supports both AMD64 and ARM64 platforms
- 🔧 **Easy Configuration**: All settings via environment variables
- 📱 **Responsive Interface**: Beautiful web interface for viewing and selecting groups

## Quick Start

### Using Docker Compose (Recommended)

1. **Clone the repository** (or copy the files to your server)

2. **Create a `.env` file** from the example:
   ```bash
   cp .env.example .env
   ```

3. **Edit the `.env` file** with your Dispatcharr credentials:
   ```bash
   DISPATCHARR_BASE_URL=http://your-dispatcharr:9191
   DISPATCHARR_USERNAME=your-username
   DISPATCHARR_PASSWORD=your-password
   ```

4. **Start the container**:
   ```bash
   docker-compose up -d
   ```

5. **Access the channel guide** at `http://localhost:9198`

### Using Pre-built Container from GitHub Container Registry

If you want to use the pre-built container instead of building it yourself:

1. **Pull the container**:
   ```bash
   docker pull ghcr.io/motwakorb/guidearr:latest
   ```

2. **Run the container**:
   ```bash
   docker run -d \
     --name guidearr \
     -p 9198:5000 \
     -e DISPATCHARR_BASE_URL=http://your-dispatcharr:9191 \
     -e DISPATCHARR_USERNAME=your-username \
     -e DISPATCHARR_PASSWORD=your-password \
     ghcr.io/motwakorb/guidearr:latest
   ```

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

### Examples

**Filter to "Family Friendly" profile:**
```env
CHANNEL_PROFILE_NAME=Family Friendly
```

**Exclude specific channel groups:**
```env
EXCLUDE_CHANNEL_GROUPS=Adult Content,Premium Sports
```

**Custom page title:**
```env
PAGE_TITLE=Smith Family TV Channels
```

**Custom cache refresh schedule:**
```env
# Refresh every 4 hours
CACHE_REFRESH_CRON=0 */4 * * *

# Refresh daily at 3 AM
CACHE_REFRESH_CRON=0 3 * * *

# Refresh every 30 minutes
CACHE_REFRESH_CRON=*/30 * * * *
```

## GitHub Setup and Automated Builds

### Prerequisites

1. A GitHub account
2. Git installed on your local machine

### Initial Repository Setup

1. **Create a new repository on GitHub**:
   - Go to https://github.com/new
   - Name it (e.g., `homelab` or `dispatcharr-channel-guide`)
   - Make it public or private (your choice)
   - Don't initialize with README (you already have files)
   - Click "Create repository"

2. **Push your code to GitHub** (if not already done):
   ```bash
   cd /Users/lecaptainc/Code/homelab

   # Initialize git if not already done
   git init
   git add .
   git commit -m "Add Dispatcharr Channel Guide application"

   # Add your GitHub repository as remote
   git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO-NAME.git

   # Push to GitHub
   git branch -M main
   git push -u origin main
   ```

### Enable GitHub Container Registry

1. **Enable GitHub Packages** (if not already enabled):
   - Go to your GitHub repository settings
   - Navigate to "Actions" → "General"
   - Under "Workflow permissions", select "Read and write permissions"
   - Check "Allow GitHub Actions to create and approve pull requests"
   - Click "Save"

2. **The GitHub Actions workflow will automatically**:
   - Build the Docker container on every push to `main` branch
   - Build for both AMD64 and ARM64 architectures
   - Push the image to GitHub Container Registry (ghcr.io)
   - Tag with `latest` and the git SHA

### Using the Auto-built Container

After your first commit is pushed and the GitHub Action completes:

1. **The container will be available at**:
   ```
   ghcr.io/YOUR-GITHUB-USERNAME/guidearr:latest
   ```

2. **Update your `docker-compose.yml`** to use the pre-built image:
   ```yaml
   version: '3.8'

   services:
     guidearr:
       image: ghcr.io/YOUR-GITHUB-USERNAME/guidearr:latest
       # Remove the 'build: .' line
       container_name: guidearr
       restart: unless-stopped
       ports:
         - "9198:5000"
       environment:
         # ... rest of your environment variables
   ```

3. **Pull and run**:
   ```bash
   docker-compose pull
   docker-compose up -d
   ```

### Viewing Build Status

- **Check GitHub Actions**: Go to your repository → "Actions" tab
- **View logs**: Click on any workflow run to see build logs
- **View packages**: Go to your GitHub profile → "Packages" to see published containers

### Manual Trigger

You can manually trigger a build from GitHub:
1. Go to repository → "Actions" tab
2. Select "Build and Push Docker Image" workflow
3. Click "Run workflow" button
4. Select branch and click "Run workflow"

## Building Locally

If you want to build the container locally instead of using the pre-built one:

```bash
cd /Users/lecaptainc/Code/homelab/powershell

# Build the container
docker build -t dispatcharr-channel-guide .

# Run it
docker run -d \
  --name dispatcharr-channel-guide \
  -p 9198:5000 \
  -e DISPATCHARR_BASE_URL=http://your-dispatcharr:9191 \
  -e DISPATCHARR_USERNAME=your-username \
  -e DISPATCHARR_PASSWORD=your-password \
  dispatcharr-channel-guide
```

## Accessing the Channel Guide

Once the container is running:

- **Main page**: http://localhost:9198
- **Health check**: http://localhost:9198/health
- **Manual refresh**: http://localhost:9198/refresh (forces immediate cache refresh)

### How Caching Works

The application uses smart caching to provide instant page loads:

1. **Startup**: When the container starts, it immediately fetches all data from Dispatcharr (channels, groups, logos) and generates the HTML
2. **Fast Serving**: All subsequent page loads are served instantly from the cache
3. **Scheduled Refresh**: The cache automatically refreshes based on your `CACHE_REFRESH_CRON` setting (default: every 6 hours)
4. **Manual Refresh**: Visit `/refresh` to force an immediate cache update

The health endpoint (`/health`) shows:
- Whether the cache is populated
- When it was last updated
- Any errors that occurred during refresh

If you're running on a different port (e.g., 8080), change the port mapping in `docker-compose.yml`:
```yaml
ports:
  - "8080:5000"
```

## Updating the Container

### Using Pre-built Image

```bash
docker-compose pull
docker-compose up -d
```

### Rebuilding Locally

```bash
docker-compose down
docker-compose build
docker-compose up -d
```

## Troubleshooting

### Container Won't Start

Check the logs:
```bash
docker-compose logs -f dispatcharr-channel-guide
```

Or for standalone container:
```bash
docker logs dispatcharr-channel-guide
```

### Authentication Errors

- Verify `DISPATCHARR_USERNAME` and `DISPATCHARR_PASSWORD` are correct
- Ensure `DISPATCHARR_BASE_URL` is reachable from the container
- If Dispatcharr is on the same host, use `http://host.docker.internal:9191` (on Mac/Windows) or the host's IP address (on Linux)

### No Channels Showing

- Check that your Dispatcharr instance has channels configured
- Verify network connectivity between the container and Dispatcharr
- Review container logs for error messages

### GitHub Actions Build Failing

- Check the "Actions" tab in your GitHub repository for error details
- Ensure you have "Read and write permissions" enabled for workflows
- Verify the `docker-build.yml` file is in `.github/workflows/` directory

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
    image: ghcr.io/YOUR-USERNAME/dispatcharr-channel-guide:latest
    ports:
      - "9198:5000"
    environment:
      - DISPATCHARR_BASE_URL=http://dispatcharr:9191
      - DISPATCHARR_USERNAME=admin
      - DISPATCHARR_PASSWORD=password
      - CHANNEL_PROFILE_NAME=Kids Safe
      - PAGE_TITLE=Kids Channels

  sports-channels:
    image: ghcr.io/YOUR-USERNAME/dispatcharr-channel-guide:latest
    ports:
      - "9199:5000"
    environment:
      - DISPATCHARR_BASE_URL=http://dispatcharr:9191
      - DISPATCHARR_USERNAME=admin
      - DISPATCHARR_PASSWORD=password
      - CHANNEL_PROFILE_NAME=Sports
      - PAGE_TITLE=Sports Channels
```

### Custom Styling

To customize the appearance, you can modify [app.py](app.py:239-400) and rebuild the container. The CSS is embedded in the `generate_html()` function.

## Security Notes

- **Never commit your `.env` file** to Git (it's in `.gitignore`)
- Use strong passwords for your Dispatcharr account
- Consider using Docker secrets for production deployments
- Run behind HTTPS in production (use nginx with Let's Encrypt)

## Support

For issues specific to:
- **Dispatcharr API**: Check Dispatcharr documentation
- **Container builds**: Check GitHub Actions logs
- **Application errors**: Check container logs with `docker-compose logs`

## License

This project is provided as-is for use with Dispatcharr instances.
