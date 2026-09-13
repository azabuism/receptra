#!/bin/bash

# RECEPTRA Image Reconstruction Script
# This script will be populated with base64 data

TARGET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Target directory: $TARGET_DIR"
echo ""
echo "This script needs base64-encoded tar data."
echo "It will be executed with: bash reconstruct_images.sh < base64_tar_data"
echo ""
echo "Expected format:"
echo "  base64_tar_data | bash reconstruct_images.sh"
echo ""
echo "Or with embedded data (via Python):"
echo "  python3 install_receptra_images.py <base64_tar_data>"

# If base64 data is piped to this script, decode and extract
if [ ! -t 0 ]; then
    echo ""
    echo "Receiving base64 data from stdin..."
    base64 -d | tar xz -C "$TARGET_DIR"
    
    # Verify
    IMAGE_COUNT=$(ls -1 "$TARGET_DIR"/category-*.{jpg,webp} 2>/dev/null | wc -l || echo "0")
    echo "✓ Installed $IMAGE_COUNT image files"
    
    ls -lh "$TARGET_DIR"/category-*.jpg | head -3
    ls -lh "$TARGET_DIR"/category-*.webp | head -3
fi

