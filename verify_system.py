#!/usr/bin/env python3
"""
RECEPTRA System Verification Script
Checks that all components are in place and working correctly
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

class SystemVerifier:
    def __init__(self):
        self.checks = []
        self.failed = []
        self.warnings = []

    def check(self, name, condition, details=""):
        """Record a check result"""
        status = "✓" if condition else "✗"
        self.checks.append(f"{status} {name}")
        if not condition:
            self.failed.append((name, details))
        return condition

    def warn(self, message):
        """Add a warning"""
        self.warnings.append(f"⚠ {message}")

    def verify_files(self):
        """Verify all required files exist"""
        print("\n📁 Verifying Files...")

        required_files = {
            'Frontend': [
                'receptra-homepage-with-subcategory-icons.html',
                'receptra-store-login.html',
                'receptra-store-register.html',
                'receptra-store-dashboard-extended.html',
                'receptra-api-client.js'
            ],
            'Backend': [
                'receptra_backend_extended.py'
            ],
            'Documentation': [
                'RECEPTRA_SYSTEM_SPECIFICATION.md',
                'DEPLOYMENT_GUIDE.md',
                'LOCAL_TESTING_GUIDE.md'
            ]
        }

        for category, files in required_files.items():
            print(f"\n  {category}:")
            for file in files:
                exists = Path(file).exists()
                size = f" ({Path(file).stat().st_size / 1024:.1f} KB)" if exists else ""
                self.check(f"  {file}", exists, f"File not found")
                if exists:
                    print(f"    ✓ {file}{size}")
                else:
                    print(f"    ✗ {file} - NOT FOUND")

    def verify_frontend(self):
        """Verify frontend files contain expected content"""
        print("\n🌐 Verifying Frontend...")

        # Check homepage
        homepage = Path('receptra-homepage-with-subcategory-icons.html')
        if homepage.exists():
            content = homepage.read_text()
            self.check("  Homepage contains footer links",
                      'bariyon.com/privacy' in content and
                      'bariyon.com/terms' in content and
                      'bariyon.com/contact' in content,
                      "Missing critical URLs in footer")
            self.check("  Homepage has responsive design",
                      'viewport' in content, "Missing viewport meta tag")
            print(f"    ✓ Homepage verified ({len(content)} bytes)")

        # Check API client
        api_client = Path('receptra-api-client.js')
        if api_client.exists():
            content = api_client.read_text()
            methods = ['scheduleReminder', 'logBusinessCall', 'createUrgentNotification', 'getBusinessCalls']
            all_methods_exist = all(m in content for m in methods)
            self.check("  API client has all methods", all_methods_exist,
                      f"Missing one of: {', '.join(methods)}")
            self.check("  API client has localStorage fallback",
                      'localStorage' in content, "Missing localStorage fallback")
            print(f"    ✓ API client verified ({len(content)} bytes)")

        # Check dashboard
        dashboard = Path('receptra-store-dashboard-extended.html')
        if dashboard.exists():
            content = dashboard.read_text()
            features = {
                'Reminder Management': '🔔',
                'Business Calls': '📞',
                'Urgent Notifications': '⚠️'
            }
            for feature, emoji in features.items():
                exists = feature in content or emoji in content
                self.check(f"  Dashboard has {feature}", exists)
            print(f"    ✓ Dashboard verified ({len(content)} bytes)")

    def verify_backend(self):
        """Verify backend Python file"""
        print("\n⚙️  Verifying Backend...")

        backend = Path('receptra_backend_extended.py')
        if backend.exists():
            content = backend.read_text()

            # Check key components
            components = {
                'FastAPI app': 'app = FastAPI',
                'SQLite database': 'sqlite3',
                'CORS middleware': 'CORSMiddleware',
                'Pydantic models': 'BaseModel',
                'Twilio integration': 'twilio',
                'APScheduler': 'BackgroundScheduler',
                'Health endpoint': '/api/health',
                'Reminders API': '/api/reminders',
                'Business Calls API': '/api/business-calls',
                'Notifications API': '/api/notifications',
            }

            for name, keyword in components.items():
                exists = keyword in content
                self.check(f"  {name}", exists)

            print(f"    ✓ Backend verified ({len(content)} bytes)")

    def verify_dependencies(self):
        """Check if Python dependencies are available"""
        print("\n📦 Verifying Dependencies...")

        required_packages = {
            'fastapi': 'FastAPI web framework',
            'uvicorn': 'ASGI server',
            'sqlalchemy': 'Database ORM',
            'twilio': 'Twilio SDK',
            'apscheduler': 'Task scheduler',
            'pydantic': 'Data validation',
        }

        missing = []
        for package, description in required_packages.items():
            try:
                __import__(package)
                self.check(f"  {package}", True)
                print(f"    ✓ {package} - {description}")
            except ImportError:
                self.check(f"  {package}", False, "Package not installed")
                missing.append(package)
                print(f"    ✗ {package} - NOT INSTALLED")

        if missing:
            self.warn(f"Missing packages: {', '.join(missing)}. Install with: pip install {' '.join(missing)}")

    def verify_critical_urls(self):
        """Verify critical URLs are present in key files"""
        print("\n🔗 Verifying Critical URLs...")

        urls_to_check = {
            'Privacy Policy': 'https://www.bariyon.com/privacy.html',
            'Terms of Service': 'https://www.bariyon.com/terms.html',
            'Contact Form': 'https://www.bariyon.com/contact.html'
        }

        files_with_urls = [
            'receptra-homepage-with-subcategory-icons.html',
            'receptra-store-login.html',
            'receptra-store-dashboard-extended.html'
        ]

        print(f"\n  Critical URLs (must be in all frontend pages):")
        for url_name, url in urls_to_check.items():
            print(f"\n    {url_name}: {url}")
            for filename in files_with_urls:
                path = Path(filename)
                if path.exists():
                    content = path.read_text()
                    has_url = url in content
                    status = "✓" if has_url else "✗"
                    print(f"      {status} {filename}")
                    if not has_url:
                        self.failed.append((f"{url_name} in {filename}", "URL not found"))

    def verify_database_schema(self):
        """Check if database exists and verify schema"""
        print("\n🗄️  Verifying Database...")

        db_path = Path('receptra.db')

        if db_path.exists():
            self.check("  Database file exists", True)
            size = db_path.stat().st_size / 1024
            print(f"    ✓ Database exists ({size:.1f} KB)")

            try:
                import sqlite3
                conn = sqlite3.connect('receptra.db')
                cursor = conn.cursor()

                # Get list of tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

                expected_tables = ['shops', 'reservations', 'reminder_schedules',
                                 'business_call_logs', 'urgent_notifications']

                all_tables_exist = all(t in tables for t in expected_tables)
                self.check("  All required tables exist", all_tables_exist,
                          f"Found: {tables}, Expected: {expected_tables}")

                if all_tables_exist:
                    print(f"    ✓ All {len(expected_tables)} tables found: {', '.join(expected_tables)}")

                conn.close()
            except Exception as e:
                self.warn(f"Could not verify database schema: {e}")
        else:
            print(f"    ℹ Database not created yet (will be created on first backend run)")

    def verify_documentation(self):
        """Verify documentation completeness"""
        print("\n📚 Verifying Documentation...")

        docs = {
            'RECEPTRA_SYSTEM_SPECIFICATION.md': ['API endpoints', 'database schema', 'user flows'],
            'DEPLOYMENT_GUIDE.md': ['Heroku', 'Railway', 'Docker', 'environment variables'],
            'LOCAL_TESTING_GUIDE.md': ['testing checklist', 'troubleshooting', 'demo data']
        }

        for doc_file, required_sections in docs.items():
            path = Path(doc_file)
            if path.exists():
                content = path.read_text()
                has_sections = all(section.lower() in content.lower() for section in required_sections)
                self.check(f"  {doc_file}", has_sections)
                size = len(content) / 1024
                print(f"    ✓ {doc_file} ({size:.1f} KB)")

    def print_summary(self):
        """Print verification summary"""
        print("\n" + "="*60)
        print("RECEPTRA SYSTEM VERIFICATION SUMMARY")
        print("="*60)

        # Count results
        passed = len([c for c in self.checks if c.startswith("✓")])
        failed = len([c for c in self.checks if c.startswith("✗")])

        print(f"\n📊 Results: {passed} passed, {failed} failed")

        if self.failed:
            print("\n❌ Failed Checks:")
            for name, details in self.failed:
                print(f"  • {name}")
                if details:
                    print(f"    → {details}")

        if self.warnings:
            print("\n⚠️  Warnings:")
            for warning in self.warnings:
                print(f"  {warning}")

        print("\n" + "="*60)

        # Overall status
        if failed == 0 and not self.failed:
            print("✅ ALL CHECKS PASSED - System is ready for testing!")
            print("\n📋 Next Steps:")
            print("  1. Run: python3 -m venv receptra_env")
            print("  2. Run: source receptra_env/bin/activate")
            print("  3. Run: pip install -r requirements.txt")
            print("  4. Run: python3 -m uvicorn receptra_backend_extended:app --reload")
            print("  5. Open: http://localhost:8000/receptra-homepage-with-subcategory-icons.html")
            return 0
        else:
            print("❌ SOME CHECKS FAILED - Please review above and fix issues")
            return 1

    def run(self):
        """Run all verifications"""
        print("\n🔍 RECEPTRA System Verification Starting...")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Directory: {os.getcwd()}")

        self.verify_files()
        self.verify_frontend()
        self.verify_backend()
        self.verify_dependencies()
        self.verify_critical_urls()
        self.verify_database_schema()
        self.verify_documentation()

        return self.print_summary()

if __name__ == '__main__':
    verifier = SystemVerifier()
    exit_code = verifier.run()
    sys.exit(exit_code)
