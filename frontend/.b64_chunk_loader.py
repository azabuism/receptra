#!/usr/bin/env python3
"""
Load base64 chunks and reconstruct tar file
"""
import os
import sys
import base64
import tarfile

def load_and_extract(chunks_dir, target_dir):
    """Load chunks from files and extract"""
    try:
        # Chunks are expected as b64_chunk_1.txt, b64_chunk_2.txt, etc.
        b64_data = ""
        
        i = 1
        while os.path.exists(os.path.join(chunks_dir, f"b64_chunk_{i}.txt")):
            with open(os.path.join(chunks_dir, f"b64_chunk_{i}.txt"), 'r') as f:
                b64_data += f.read()
            print(f"✓ Loaded chunk {i}")
            i += 1
        
        if not b64_data:
            print("✗ No chunks found")
            return False
        
        # Decode
        print("Decoding base64...")
        tar_data = base64.b64decode(b64_data)
        
        # Extract
        print(f"Extracting to {target_dir}...")
        import io
        with tarfile.open(fileobj=io.BytesIO(tar_data), mode='r:gz') as tar:
            tar.extractall(path=target_dir)
        
        # Verify
        images = [f for f in os.listdir(target_dir) 
                 if f.startswith('category-') and f.endswith(('.jpg', '.webp'))]
        print(f"✓ Installed {len(images)} image files")
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

if __name__ == "__main__":
    chunks_dir = os.path.expanduser("~")
    target_dir = os.path.expanduser("~/mnt/www/receptra/frontend")
    
    load_and_extract(chunks_dir, target_dir)

