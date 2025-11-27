#!/usr/bin/env python3
"""
AI Test Runner - Compiles, executes, and provides coverage for AI-generated tests
"""

import os
import sys
import argparse
import shutil
import subprocess
from pathlib import Path
import glob
import re

# Import DependencyAnalyzer from ai-c-test-generator
sys.path.append(str(Path(__file__).parent.parent.parent / "ai-c-test-generator"))
from ai_c_test_generator.analyzer import DependencyAnalyzer


class AITestRunner:
    """AI Test Runner - Builds, executes, and covers AI-generated tests"""

    def __init__(self, repo_path: str, output_dir: str = "build"):
        self.repo_path = Path(repo_path).resolve()
        self.output_dir = self.repo_path / output_dir
        self.tests_dir = self.repo_path / "tests"
        self.verification_dir = self.tests_dir / "compilation_report"
        self.test_reports_dir = self.tests_dir / "test_reports"
        self.source_dir = self.repo_path / "src"

        # Initialize dependency analyzer
        self.analyzer = DependencyAnalyzer(str(self.repo_path))

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Create test reports directory
        self.test_reports_dir.mkdir(parents=True, exist_ok=True)

    def get_stubbed_functions_in_test(self, test_file_path: str) -> set:
        """Detect function stubs in a test file by parsing function definitions"""
        stubbed_functions = set()
        try:
            with open(test_file_path, 'r') as f:
                content = f.read()

            # Match function definitions like: float raw_to_celsius(int raw) {
            # Capture the function name (second word), not the return type
            matches = re.findall(r'\b\w+\s+(\w+)\s*\([^)]*\)\s*{', content)
            stubbed_functions = set(matches)

            # Remove test functions (they start with "test_")
            stubbed_functions = {func for func in stubbed_functions if not func.startswith('test_')}

        except Exception as e:
            print(f"Warning: Could not parse stubs from {test_file_path}: {e}")

        return stubbed_functions

    def find_compilable_tests(self):
        """Find test files that have compiles_yes in verification reports"""
        compilable_tests = []

        if not self.verification_dir.exists():
            print(f"❌ Verification report directory not found: {self.verification_dir}")
            return compilable_tests

        # Find all compiles_yes files
        for report_file in self.verification_dir.glob("*compiles_yes.txt"):
            # Extract test filename from report filename
            # Format: test_filename_compiles_yes.txt -> test_filename.c
            base_name = report_file.stem.replace("_compiles_yes", "")
            test_file = self.tests_dir / f"{base_name}.c"

            if test_file.exists():
                # Return the full Path object for file operations
                compilable_tests.append(test_file)
                print(f"✅ Found compilable test: {test_file.name}")
            else:
                print(f"⚠️  Test file not found: {test_file.name}")

        return compilable_tests

    def run(self):
        """Run the complete test execution pipeline"""
        print("🚀 Starting AI Test Runner...")

        # Find compilable tests
        test_files = self.find_compilable_tests()
        if not test_files:
            print("❌ No compilable tests found")
            return False

        # Copy Unity framework
        if not self.copy_unity_framework():
            print("❌ Failed to setup Unity framework")
            return False

        # Create CMakeLists.txt
        if not self.create_cmake_lists(test_files):
            print("❌ Failed to create CMakeLists.txt")
            return False

        # Build tests
        if not self.build_tests():
            print("❌ Failed to build tests")
            return False

        # Run tests
        test_results = self.run_tests()
        if not test_results:
            print("❌ No tests were executed")
            return False

        # Generate test reports
        self.generate_test_reports(test_results)

        # Generate coverage (optional)
        self.generate_coverage()

        # Summary
        total_tests = len(test_results)
        passed_tests = sum(1 for result in test_results if result['success'])
        print(f"\n📊 Test Summary: {passed_tests}/{total_tests} test suites passed")

        return passed_tests == total_tests

    def copy_unity_framework(self):
        """Copy or download Unity framework"""
        unity_dest = self.output_dir / "unity"

        # First try to copy from reference location
        unity_source = self.repo_path.parent / "ai-test-gemini-CLI" / "unity"
        if unity_source.exists() and any(unity_source.rglob("*.c")):
            if unity_dest.exists():
                try:
                    shutil.rmtree(unity_dest)
                except (OSError, PermissionError):
                    print(f"⚠️  Could not remove existing unity directory: {unity_dest}")
            shutil.copytree(unity_source, unity_dest)
            print("✅ Copied Unity framework from reference")
            return

        # If not available, download Unity
        print("📥 Downloading Unity framework...")
        import urllib.request
        import zipfile
        import tempfile

        try:
            # Download Unity from GitHub
            unity_url = "https://github.com/ThrowTheSwitch/Unity/archive/refs/heads/master.zip"
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                urllib.request.urlretrieve(unity_url, temp_zip.name)

                # Extract Unity
                with zipfile.ZipFile(temp_zip.name, 'r') as zip_ref:
                    # Extract only the src directory
                    for member in zip_ref.namelist():
                        if member.startswith('Unity-master/src/'):
                            # Remove the Unity-master/src/ prefix
                            target_path = member.replace('Unity-master/src/', 'src/')
                            if target_path.endswith('/'):
                                (unity_dest / target_path).mkdir(parents=True, exist_ok=True)
                            else:
                                zip_ref.extract(member, unity_dest.parent / "temp_unity")
                                source_file = unity_dest.parent / "temp_unity" / member
                                target_file = unity_dest / target_path
                                target_file.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(source_file, target_file)

                # Clean up
                import os
                os.unlink(temp_zip.name)
                temp_dir = unity_dest.parent / "temp_unity"
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)

            print("✅ Downloaded Unity framework")

        except Exception as e:
            print(f"❌ Failed to download Unity: {e}")
            print("⚠️  Unity framework not available, tests may not compile")

    def create_cmake_lists(self, test_files):
        cmake_content = "cmake_minimum_required(VERSION 3.10)\n"
        cmake_content += "project(Tests C)\n\n"
        cmake_content += "set(CMAKE_C_STANDARD 99)\n"
        cmake_content += "add_definitions(-DUNIT_TEST)\n\n"
        cmake_content += "set(CMAKE_C_FLAGS \"${CMAKE_C_FLAGS} --coverage\")\n"
        cmake_content += "set(CMAKE_EXE_LINKER_FLAGS \"${CMAKE_EXE_LINKER_FLAGS} --coverage\")\n\n"
        cmake_content += "include_directories(unity/src)\n"
        cmake_content += "include_directories(src)\n\n"

# (Truncated - CLI contains many more lines)
