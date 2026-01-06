#!/usr/bin/env python3
"""
AI Test Runner - Compiles, executes, and provides coverage for AI-generated C and C++ unit tests
"""

import os
import sys
import argparse
import shutil
import subprocess
from pathlib import Path
import glob
import re


def _enforce_manual_review_gate(repo_root: Path) -> None:
    """MANDATORY HUMAN REVIEW GATE — DO NOT BYPASS.

    Blocks any build/run unless per-test approval flag(s) exist and match required content.
    On failure, prints the exact required message and exits non-zero.
    """

    review_dir = repo_root / "tests" / "review"
    review_required_path = review_dir / "review_required.md"
    required = "approved = true\nreviewed_by = <human_name>\ndate = <ISO date>\n"

    def _parse_generated_test_files(path: Path) -> list[Path]:
        try:
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        except Exception:
            return []

        lines = text.split("\n")
        in_section = False
        generated: list[Path] = []

        for line in lines:
            if line.strip() == "## Generated test files":
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if not in_section:
                continue

            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            item = stripped.lstrip("-").strip()
            if not item or item == "(none)":
                continue

            # Normalize separators and interpret as repo-relative.
            item = item.replace("\\", "/")
            generated.append(repo_root / Path(item))

        return generated

    generated_test_files = _parse_generated_test_files(review_required_path)
    if not generated_test_files:
        print("❌ Manual review not approved. Build and execution halted.")
        raise SystemExit(3)

    for test_path in generated_test_files:
        approval_name = f"APPROVED.{test_path.name}.flag"
        approved_path = review_dir / approval_name
        try:
            content = approved_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        except Exception:
            print("❌ Manual review not approved. Build and execution halted.")
            raise SystemExit(3)

        if content != required:
            print("❌ Manual review not approved. Build and execution halted.")
            raise SystemExit(3)




class AITestRunner:
    """AI Test Runner - Builds, executes, and covers AI-generated C and C++ tests"""

    def __init__(self, repo_path: str, output_dir: str = "build", language: str = "auto"):
        self.repo_path = Path(repo_path).resolve()
        out = Path(output_dir)
        if out.is_absolute():
            self.output_dir = out
        else:
            # Enforce build output under <repo>/tests/ to avoid separate top-level build folders.
            # Examples:
            #   output_dir=build         -> <repo>/tests/build
            #   output_dir=tests/build   -> <repo>/tests/build
            #   output_dir=ai_test_build -> <repo>/tests/ai_test_build
            if out.parts[:1] == ("tests",):
                self.output_dir = self.repo_path / out
            else:
                self.output_dir = self.repo_path / "tests" / out
        self.tests_dir = self.repo_path / "tests"
        self.verification_dir = self.tests_dir / "compilation_report"
        self.test_reports_dir = self.tests_dir / "test_reports"
        self.source_dir = self.repo_path / "src"
        self.language = language  # "c", "cpp", or "auto"

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Create test reports directory
        self.test_reports_dir.mkdir(parents=True, exist_ok=True)

    def detect_language(self, test_files):
        """Detect the programming language from test files"""
        if self.language != "auto":
            return self.language

        # Check file extensions
        cpp_extensions = ['.cpp', '.cc', '.cxx', '.c++']
        c_extensions = ['.c']

        has_cpp = any(any(test_file.name.endswith(ext) for ext in cpp_extensions) for test_file in test_files)
        has_c = any(any(test_file.name.endswith(ext) for ext in c_extensions) for test_file in test_files)

        if has_cpp:
            return "cpp"
        elif has_c:
            return "c"
        else:
            return "cpp"  # Default to C++

    def find_compilable_tests(self):
        """Find test files that have compiles_yes in verification reports"""
        print("Starting find_compilable_tests")
        compilable_tests = []

        if not self.verification_dir.exists():
            print(f"❌ Verification report directory not found: {self.verification_dir}")
            return compilable_tests

        # Find all compiles_yes files
        for report_file in self.verification_dir.glob("*compiles_yes.txt"):
            # Extract test filename from report filename
            base_name = report_file.stem.replace("_compiles_yes", "")
            
            # Try both .c and .cpp extensions
            for ext in ['.c', '.cpp', '.cc', '.cxx', '.c++']:
                test_file = self.tests_dir / f"{base_name}{ext}"
                if test_file.exists():
                    compilable_tests.append(test_file)
                    print(f"✅ Found compilable test: {test_file.name}")
                    break

        return compilable_tests

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
            return True

        # If not available, download Unity
        print("📥 Downloading Unity framework...")
        import urllib.request
        import zipfile
        import tempfile

        try:
            # Download Unity from GitHub
            unity_url = "https://github.com/ThrowTheSwitch/Unity/archive/refs/heads/master.zip"
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                temp_zip_path = temp_zip.name

            # Download to the temp file
            urllib.request.urlretrieve(unity_url, temp_zip_path)

            # Extract Unity
            with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
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
            os.unlink(temp_zip_path)
            temp_dir = unity_dest.parent / "temp_unity"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

            print("✅ Downloaded Unity framework")
            return True

        except Exception as e:
            print(f"❌ Failed to download Unity: {e}")
            print("⚠️  Unity framework not available, tests may not compile")
            return False

    def setup_cpp_framework(self):
        """Setup C++ test framework (Google Test) and Arduino stubs"""
        print("📦 Setting up C++ test framework...")

        # Copy Google Test header
        gtest_dest = self.output_dir / "gtest" / "gtest.h"
        gtest_dest.parent.mkdir(parents=True, exist_ok=True)

        # Try to copy from reference location first
        gtest_source = self.repo_path.parent / "Door-Monitoring" / "tests_and_build_single_file" / "gtest" / "gtest.h"
        if gtest_source.exists():
            shutil.copy2(gtest_source, gtest_dest)
            print("✅ Copied Google Test header from reference")
        else:
            # Create minimal Google Test framework
            gtest_content = '''#pragma once
// Minimal Google Test-like framework for testing
#include <iostream>
#include <vector>
#include <functional>
#include <cassert>

class TestRegistry {
private:
    struct TestInfo {
        std::string name;
        std::function<void()> func;
    };
    std::vector<TestInfo> tests_;
    static TestRegistry* instance_;

    TestRegistry() {}

public:
    static TestRegistry& instance() {
        if (!instance_) instance_ = new TestRegistry();
        return *instance_;
    }

    void register_test(const std::string& name, std::function<void()> func) {
        tests_.push_back({name, func});
    }

    int run_all_tests() {
        int failures = 0;
        for (const auto& test : tests_) {
            try {
                test.func();
                std::cout << "[ PASS ] " << test.name << std::endl;
            } catch (const std::exception& e) {
                std::cout << "[ FAIL ] " << test.name << ": " << e.what() << std::endl;
                failures++;
            } catch (...) {
                std::cout << "[ FAIL ] " << test.name << ": Unknown exception" << std::endl;
                failures++;
            }
        }
        return failures;
    }
};

TestRegistry* TestRegistry::instance_ = nullptr;

#define TEST(suite, name) \\
    void Test_##suite##_##name(); \\
    struct Registrar_##suite##_##name { \\
        Registrar_##suite##_##name() { \\
            TestRegistry::instance().register_test(#suite "." #name, Test_##suite##_##name); \\
        } \\
    } registrar_##suite##_##name; \\
    void Test_##suite##_##name()

#define ASSERT_EQ(a, b) assert((a) == (b))
#define ASSERT_NE(a, b) assert((a) != (b))
#define ASSERT_TRUE(a) assert((a))
#define ASSERT_FALSE(a) assert(!(a))

int RUN_ALL_TESTS() {
    return TestRegistry::instance().run_all_tests();
}
'''
            with open(gtest_dest, 'w') as f:
                f.write(gtest_content)
            print("✅ Created minimal Google Test framework")

        # Copy Arduino stubs
        arduino_dest = self.output_dir / "arduino_stubs"
        arduino_dest.mkdir(parents=True, exist_ok=True)

        # Try to copy from reference location
        arduino_source = self.repo_path.parent / "Door-Monitoring" / "tests_and_build_single_file"
        stubs_files = ["Arduino_stubs.h", "Arduino_stubs.cpp"]
        copied = False

        for stub_file in stubs_files:
            source_file = arduino_source / stub_file
            if source_file.exists():
                shutil.copy2(source_file, arduino_dest / stub_file)
                copied = True

        if copied:
            print("✅ Copied Arduino stubs from reference")
        else:
            # Create Arduino stubs with expected globals for testing
            arduino_h_content = '''#pragma once

#include <string>
#include <vector>
#include <iostream>
#include <chrono>
#include <thread>

void digitalWrite(int pin, int value);
int digitalRead(int pin);
void pinMode(int pin, int mode);
void delay(int ms);
unsigned long millis();
void reset_arduino_stubs();

class String {
private:
    std::string data;

public:
    String();
    String(const char* str);
    String(int val);
    String& operator+=(const char* str);
    String operator+(const char* str) const;
    String operator+(const String& other) const;
    const char* c_str() const;
    
    friend String operator+(const char* lhs, const String& rhs);
};

struct DigitalWriteCall {
    int pin;
    int value;
};

struct DelayCall {
    int ms;
};

class SerialClass {
public:
    void begin(int baud);
    void print(const char* str);
    void println(const char* str);
    void print(int val);
    void println(int val);
    void print(const String& str);
    void println(const String& str);
    
    int begin_call_count = 0;
    int last_baud_rate = 0;
    int println_call_count = 0;
    int print_call_count = 0;
    
    std::string outputBuffer;
};

extern SerialClass Serial;
extern std::vector<DigitalWriteCall> digitalWrite_calls;
extern std::vector<DelayCall> delay_calls;

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define LED 13
'''

            arduino_cpp_content = '''#include "Arduino_stubs.h"
#include <iostream>
#include <map>
#include <chrono>

static std::map<int, int> pin_states;
static auto start_time = std::chrono::steady_clock::now();

std::vector<DigitalWriteCall> digitalWrite_calls;
std::vector<DelayCall> delay_calls;

void reset_arduino_stubs() {
    Serial.begin_call_count = 0;
    Serial.last_baud_rate = 0;
    Serial.println_call_count = 0;
    Serial.print_call_count = 0;
    digitalWrite_calls.clear();
    delay_calls.clear();
    Serial.outputBuffer.clear();
    pin_states.clear();
}

void digitalWrite(int pin, int value) {
    pin_states[pin] = value;
    digitalWrite_calls.push_back({pin, value});
}

int digitalRead(int pin) {
    return pin_states[pin];
}

void pinMode(int pin, int mode) {
    // Not tracked for testing
}

void delay(int ms) {
    delay_calls.push_back({ms});
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

unsigned long millis() {
    auto now = std::chrono::steady_clock::now();
    auto duration = now - start_time;
    return std::chrono::duration_cast<std::chrono::milliseconds>(duration).count();
}

SerialClass Serial;

void SerialClass::begin(int baud) {
    begin_call_count++;
    last_baud_rate = baud;
}

void SerialClass::print(const char* str) {
    print_call_count++;
    outputBuffer += str;
}

void SerialClass::println(const char* str) {
    println_call_count++;
    outputBuffer += str;
    outputBuffer += "\\n";
}

void SerialClass::print(int val) {
    print_call_count++;
    outputBuffer += std::to_string(val);
}

void SerialClass::println(int val) {
    println_call_count++;
    outputBuffer += std::to_string(val);
    outputBuffer += "\\n";
}

void SerialClass::print(const String& str) {
    print_call_count++;
    outputBuffer += str.c_str();
}

void SerialClass::println(const String& str) {
    println_call_count++;
    outputBuffer += str.c_str();
    outputBuffer += "\\n";
}

String::String() {}

String::String(const char* str) : data(str) {}

String::String(int val) : data(std::to_string(val)) {}

String& String::operator+=(const char* str) {
    data += str;
    return *this;
}

String String::operator+(const char* str) const {
    String result = *this;
    result.data += str;
    return result;
}

String String::operator+(const String& other) const {
    String result = *this;
    result.data += other.data;
    return result;
}

String operator+(const char* lhs, const String& rhs) {
    String result(lhs);
    result.data += rhs.data;
    return result;
}

const char* String::c_str() const {
    return data.c_str();
}
'''
            with open(arduino_dest / "Arduino_stubs.h", 'w') as f:
                f.write(arduino_h_content)
            with open(arduino_dest / "Arduino_stubs.cpp", 'w') as f:
                f.write(arduino_cpp_content)
            print("✅ Created minimal Arduino stubs")

        return True

    def copy_source_files(self):
        """Copy source files to build directory and generate headers"""
        src_build_dir = self.output_dir / "src"
        src_build_dir.mkdir(exist_ok=True)

        if self.source_dir.exists():
            # Copy C and C++ source files
            source_files = list(self.source_dir.glob("*.c")) + list(self.source_dir.glob("*.cpp"))
            
            for src_file in source_files:
                with open(src_file, 'r') as f:
                    content = f.read()
                
                # Rename main() to app_main() to allow testing it without conflicts
                import re
                if 'int main' in content:
                    content = re.sub(r'\bint\s+main\s*\(', 'int app_main(', content)
                    print(f"🔄 Renamed main() to app_main() in {src_file.name}")
                
                # Write to build directory
                dest_file = src_build_dir / src_file.name
                with open(dest_file, 'w') as f:
                    f.write(content)
                print(f"📋 Copied source: {src_file.name}")
                
                # Generate a header file (only for C files usually, but maybe useful for CPP too if missing)
                if src_file.suffix == '.c':
                    header_file = src_build_dir / (src_file.stem + ".h")
                    self._generate_header_from_source(src_file, header_file)

            for header_file in self.source_dir.glob("*.h"):
                shutil.copy2(header_file, src_build_dir)
                print(f"📋 Copied header: {header_file.name}")
        else:
            print(f"⚠️  Source directory not found: {self.source_dir}")

    def _generate_header_from_source(self, src_file, dest_header):
        """Generate a header file from source with function declarations"""
        try:
            with open(src_file, 'r') as f:
                content = f.read()
            
            # Extract function definitions (anything that looks like a function)
            import re
            # Match patterns like: return_type function_name(parameters) {
            pattern = r'(\w+\s+(\w+)\s*\([^)]*\))\s*\{'
            matches = re.findall(pattern, content)
            
            if matches:
                with open(dest_header, 'w') as f:
                    f.write(f"/* Auto-generated header for {src_file.name} */\n")
                    f.write("#pragma once\n\n")
                    f.write("#include <stdint.h>\n")
                    f.write("#include <stdbool.h>\n")
                    f.write("#include <stdlib.h>\n\n")
                    
                    for match in matches:
                        func_name = match[1]
                        func_decl = match[0]
                        # Skip main function
                        if func_name != 'main':
                            f.write(f"{func_decl};\n")
                print(f"📝 Generated header: {dest_header.name}")
        except Exception as e:
            print(f"⚠️  Could not generate header: {e}")

    def copy_test_files(self, test_files):
        """Copy test files to build directory"""
        tests_build_dir = self.output_dir / "tests"
        tests_build_dir.mkdir(exist_ok=True)
        import re

        for test_file in test_files:
            with open(test_file, 'r') as f:
                content = f.read()
            
            # For C tests, inject #include for the source C file to get implementations
            if test_file.name.endswith('.c') and '#include "unity.h"' in content:
                # Determine source filename
                test_name = test_file.stem
                if test_name.startswith('test_'):
                    source_name = test_name[5:]
                else:
                    source_name = test_name
                
                # Include the actual source C file (not header)
                # Since test is in tests/ and source is in src/, use ../src/filename.c
                source_file = f"../src/{source_name}.c"
                if f'#include "{source_file}"' not in content and f'#include "../src/' not in content:
                    # Find where to insert (after unity.h include)
                    unity_pos = content.find('#include "unity.h"')
                    if unity_pos != -1:
                        # Find the end of the line
                        eol = content.find('\n', unity_pos) + 1
                        # Insert the include
                        content = content[:eol] + f'#include "{source_file}"\n' + content[eol:]
                        print(f"📝 Added #include for {source_file} to test file")
                
                # Replace calls to main() with app_main() to avoid recursion
                # But don't replace the test runner's main definition (int main(void))
                if 'app_main(' not in content:
                    # Regex: match 'main(' not preceded by 'int ' or 'void '
                    new_content = re.sub(r'(?<!\bint\s)(?<!\bvoid\s)\bmain\s*\(', 'app_main(', content)
                    if new_content != content:
                        content = new_content
                        print(f"🔄 Replaced main() calls with app_main() in {test_file.name}")
            
            # Write the modified test file
            dest_file = tests_build_dir / test_file.name
            with open(dest_file, 'w') as f:
                f.write(content)
            print(f"📋 Copied test: {test_file.name}")

    def create_cmake_lists(self, test_files, language):
        """Create CMakeLists.txt based on language"""
        if language == "cpp":
            return self.create_cpp_cmake_lists(test_files)
        else:
            return self.create_c_cmake_lists(test_files)

    def create_c_cmake_lists(self, test_files):
        """Create CMakeLists.txt for C tests with Unity"""
        cmake_content = "cmake_minimum_required(VERSION 3.10)\n"
        cmake_content += "project(Tests C)\n\n"
        cmake_content += "set(CMAKE_C_STANDARD 99)\n"
        cmake_content += "add_definitions(-DUNIT_TEST)\n\n"
        cmake_content += "set(CMAKE_C_FLAGS \"${CMAKE_C_FLAGS} --coverage\")\n"
        cmake_content += "set(CMAKE_EXE_LINKER_FLAGS \"${CMAKE_EXE_LINKER_FLAGS} --coverage\")\n\n"
        cmake_content += "include_directories(unity/src)\n"
        cmake_content += "include_directories(src)\n\n"
        cmake_content += "add_library(unity unity/src/unity.c)\n\n"

        for test_file in test_files:
            test_name = os.path.splitext(os.path.basename(test_file))[0]
            executable_name = test_name

            # For C tests with Unity, only compile the test file
            # The test file should include the source file to get function definitions
            test_file_basename = os.path.basename(test_file).replace('\\', '/')
            cmake_content += f"add_executable({executable_name} tests/{test_file_basename})\n"
            cmake_content += f"target_link_libraries({executable_name} unity)\n\n"

        with open(os.path.join(self.output_dir, 'CMakeLists.txt'), 'w') as f:
            f.write(cmake_content)
        print(f"✅ Created CMakeLists.txt for C tests with {len(test_files)} targets")
        return True

    def create_cpp_cmake_lists(self, test_files):
        """Create CMakeLists.txt for C++ tests with Google Test"""
        cmake_content = "cmake_minimum_required(VERSION 3.14)\n"
        cmake_content += "project(cpp_tests CXX)\n\n"
        cmake_content += "set(CMAKE_CXX_STANDARD 17)\n"
        cmake_content += "set(CMAKE_CXX_STANDARD_REQUIRED ON)\n\n"
        cmake_content += "enable_testing()\n\n"

        # Add source files under test
        source_files = []
        if self.source_dir.exists():
            for ext in ['.cpp', '.cc', '.cxx', '.c++']:
                source_files.extend(self.source_dir.glob(f"*{ext}"))

        if source_files:
            cmake_content += "# Source code under test\n"
            cmake_content += "add_library(test_lib OBJECT\n"
            for src_file in source_files:
                cmake_content += f"  src/{src_file.name}\n"
            cmake_content += ")\n"
            cmake_content += "target_include_directories(test_lib PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)\n"
            cmake_content += "target_include_directories(test_lib PUBLIC arduino_stubs)\n"
            cmake_content += "target_include_directories(test_lib PUBLIC gtest)\n\n"

        # Add test executables
        for test_file in test_files:
            test_name = test_file.stem
            cmake_content += f"# Test executable for {test_name}\n"
            cmake_content += f"add_executable({test_name}\n"
            cmake_content += f"  tests/{test_file.name}\n"
            cmake_content += f"  arduino_stubs/Arduino_stubs.cpp\n"
            if source_files:
                cmake_content += f"  $<TARGET_OBJECTS:test_lib>\n"
            cmake_content += ")\n"
            cmake_content += f"target_include_directories({test_name} PRIVATE ${{CMAKE_CURRENT_SOURCE_DIR}})\n"
            cmake_content += f"target_include_directories({test_name} PRIVATE ${{CMAKE_CURRENT_SOURCE_DIR}}/src)\n"
            cmake_content += f"target_include_directories({test_name} PRIVATE arduino_stubs)\n"
            cmake_content += f"target_include_directories({test_name} PRIVATE gtest)\n\n"
            cmake_content += f"add_test(\n"
            cmake_content += f"  NAME {test_name}\n"
            cmake_content += f"  COMMAND {test_name}\n"
            cmake_content += ")\n\n"

        # Write CMakeLists.txt
        cmake_file = self.output_dir / "CMakeLists.txt"
        with open(cmake_file, 'w') as f:
            f.write(cmake_content)

        print("✅ Created CMakeLists.txt for C++ tests")
        return True

    def build_tests(self):
        """Build the tests using CMake"""
        print("🔨 Building tests...")

        try:
            # Configure with CMake
            result = subprocess.run(
                ["cmake", "."],
                cwd=self.output_dir,
                capture_output=True,
                text=True,
                check=True
            )
            print("✅ CMake configuration successful")

            # Build with cmake --build
            result = subprocess.run(
                ["cmake", "--build", "."],
                cwd=self.output_dir,
                capture_output=True,
                text=True,
                check=True
            )
            print("✅ Build successful")

        except subprocess.CalledProcessError as e:
            print(f"❌ Build failed: {e}")
            print(f"STDOUT: {e.stdout}")
            print(f"STDERR: {e.stderr}")
            return False
        except FileNotFoundError:
            print("❌ CMake not found. Please install CMake.")
            return False

        return True

    def run_tests(self):
        """Run the compiled tests"""
        print("🧪 Running tests...")

        test_results = []
        test_executables = [exe for exe in self.output_dir.glob("*test*") 
                           if exe.is_file() and exe.suffix in ['.exe', ''] and 'CTest' not in exe.name]

        if not test_executables:
            print("❌ No test executables found")
            return test_results

        for exe in test_executables:
            if exe.is_file() and os.access(exe, os.X_OK):
                print(f"   Running {exe.name}...")
                try:
                    result = subprocess.run(
                        [str(exe)],
                        cwd=self.output_dir,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )

                    # Parse test output
                    individual_tests = 0
                    individual_passed = 0
                    individual_failed = 0

                    for line in result.stdout.split('\n'):
                        line = line.strip()
                        if ':PASS' in line:
                            individual_tests += 1
                            individual_passed += 1
                        elif ':FAIL' in line:
                            individual_tests += 1
                            individual_failed += 1
                        elif line.endswith('Tests') and 'Failures' in line:
                            parts = line.split()
                            if len(parts) >= 3:
                                try:
                                    individual_tests = int(parts[0])
                                    individual_failed = int(parts[2])
                                    individual_passed = individual_tests - individual_failed
                                except ValueError:
                                    pass

                    success = result.returncode == 0
                    test_results.append({
                        'name': exe.name,
                        'success': success,
                        'output': result.stdout,
                        'errors': result.stderr,
                        'returncode': result.returncode,
                        'individual_tests': individual_tests,
                        'individual_passed': individual_passed,
                        'individual_failed': individual_failed
                    })

                    status = "✅" if success else "❌"
                    if individual_tests > 0:
                        print(f"   {status} {exe.name} ({individual_passed}/{individual_tests} tests passed)")
                    else:
                        print(f"   {status} {exe.name} (exit code: {result.returncode})")

                except subprocess.TimeoutExpired:
                    test_results.append({
                        'name': exe.name,
                        'success': False,
                        'output': '',
                        'errors': 'Test timed out',
                        'returncode': -1,
                        'individual_tests': 0,
                        'individual_passed': 0,
                        'individual_failed': 0
                    })
                    print(f"   ⏰ {exe.name} timed out")

                except Exception as e:
                    test_results.append({
                        'name': exe.name,
                        'success': False,
                        'output': '',
                        'errors': str(e),
                        'returncode': -1,
                        'individual_tests': 0,
                        'individual_passed': 0,
                        'individual_failed': 0
                    })
                    print(f"   ❌ {exe.name} failed: {e}")

        return test_results

    def generate_test_reports(self, test_results):
        """Generate individual test reports"""
        print(f"📝 Generating test reports in {self.test_reports_dir}...")

        for result in test_results:
            report_file = self.test_reports_dir / f"{result['name']}_report.txt"

            with open(report_file, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write(f"TEST REPORT: {result['name']}\n")
                f.write("=" * 60 + "\n\n")

                f.write("EXECUTION SUMMARY\n")
                f.write("-" * 20 + "\n")
                f.write(f"Test Executable: {result['name']}\n")
                f.write(f"Exit Code: {result['returncode']}\n")
                f.write(f"Overall Status: {'PASSED' if result['success'] else 'FAILED'}\n")
                f.write(f"Individual Tests Run: {result['individual_tests']}\n")
                f.write(f"Individual Tests Passed: {result['individual_passed']}\n")
                f.write(f"Individual Tests Failed: {result['individual_failed']}\n\n")

                if result['errors']:
                    f.write("ERRORS\n")
                    f.write("-" * 10 + "\n")
                    f.write(f"{result['errors']}\n\n")

                f.write("DETAILED OUTPUT\n")
                f.write("-" * 20 + "\n")
                if result['output']:
                    f.write(result['output'])
                else:
                    f.write("(No output captured)\n")

                f.write("\n" + "=" * 60 + "\n")

            print(f"   📄 Generated report: {report_file.name}")

    def generate_coverage(self):
        """Generate coverage reports (placeholder)"""
        print("📊 Coverage reporting not yet implemented")
        return True

    def create_cmake_lists(self, test_files, language):
        """Create CMakeLists.txt in repo root for CMake build"""
        cmake_path = self.repo_path / "CMakeLists.txt"
        with open(cmake_path, 'w') as f:
            f.write("cmake_minimum_required(VERSION 3.14)\n")
            f.write("project(TestProject CXX)\n\n")
            
            f.write("set(CMAKE_CXX_STANDARD 17)\n")
            f.write("set(CMAKE_CXX_STANDARD_REQUIRED ON)\n\n")
            
            f.write("# Enable testing\n")
            f.write("enable_testing()\n\n")
            
            if language == "cpp":
                f.write("# Google Test setup\n")
                # Disable SSL verification for download to avoid certificate issues
                f.write("set(CMAKE_TLS_VERIFY 0)\n")
                f.write("include(FetchContent)\n")
                f.write("FetchContent_Declare(\n")
                f.write("  googletest\n")
                f.write("  URL https://github.com/google/googletest/archive/refs/tags/v1.14.0.zip\n")
                f.write(")\n")
                f.write("# For Windows: Prevent overriding the parent project's compiler/linker settings\n")
                f.write("set(gtest_force_shared_crt ON CACHE BOOL \"\" FORCE)\n")
                f.write("FetchContent_MakeAvailable(googletest)\n\n")
                
                f.write("include_directories(${gtest_SOURCE_DIR}/include)\n\n")

            for test_file in test_files:
                # Assume test files are in tests/ directory
                exe_name = test_file.stem
                
                # Determine source file name (assuming test_X.cpp tests X.cpp)
                source_name = test_file.stem
                if source_name.startswith("test_"):
                    source_name = source_name[5:]
                
                # Look for source file in src/ or root
                source_file_path = self.repo_path / "src" / f"{source_name}.cpp"
                if not source_file_path.exists():
                     # Try root
                     source_file_path = self.repo_path / f"{source_name}.cpp"
                
                # If source file exists, create a library for it
                if source_file_path.exists():
                    lib_name = f"{source_name}_lib"
                    # Use relative path for CMake
                    rel_source_path = source_file_path.relative_to(self.repo_path).as_posix()
                    
                    f.write(f"add_library({lib_name} OBJECT {rel_source_path})\n")
                    f.write(f"target_include_directories({lib_name} PUBLIC ${{CMAKE_CURRENT_SOURCE_DIR}})\n")
                    f.write(f"target_include_directories({lib_name} PUBLIC ${{CMAKE_BINARY_DIR}}/arduino_stubs)\n\n")
                    
                    f.write(f"add_executable({exe_name} tests/{test_file.name} ${{CMAKE_BINARY_DIR}}/arduino_stubs/Arduino_stubs.cpp $<TARGET_OBJECTS:{lib_name}>)\n")
                else:
                    # Fallback: just compile test file (might fail linking)
                    f.write(f"add_executable({exe_name} tests/{test_file.name} ${{CMAKE_BINARY_DIR}}/arduino_stubs/Arduino_stubs.cpp)\n")

                if language == "cpp":
                    f.write(f"target_link_libraries({exe_name} GTest::gtest_main)\n")
                
                f.write(f"target_include_directories({exe_name} PRIVATE ${{CMAKE_CURRENT_SOURCE_DIR}} ${{CMAKE_CURRENT_SOURCE_DIR}}/src ${{CMAKE_BINARY_DIR}}/arduino_stubs)\n")
                
                f.write(f"add_test(NAME {exe_name} COMMAND {exe_name})\n")
                
        print(f"📝 Created CMakeLists.txt at {cmake_path}")
        return True

    def run(self):
        """Run the complete test execution pipeline"""
        print("🚀 Starting AI Test Runner...")

        # Find compilable tests
        test_files = self.find_compilable_tests()
        if not test_files:
            print("❌ No compilable tests found")
            return False

        # Detect language
        language = self.detect_language(test_files)
        print(f"🔍 Detected language: {language.upper()}")

        # Setup test framework based on language
        if language == "cpp":
            if not self.setup_cpp_framework():
                print("❌ Failed to setup C++ test framework")
                return False
        else:  # C
            if not self.copy_unity_framework():
                print("❌ Failed to setup Unity framework")
                return False

        # Copy source and test files
        self.copy_source_files()
        self.copy_test_files(test_files)

        # Create CMakeLists.txt
        # self.create_cmake_lists(test_files, language)  # Project already has CMakeLists.txt

        # Configure CMake
        print("🔧 Configuring CMake...")
        try:
            subprocess.run(["cmake", "-S", str(self.repo_path), "-B", str(self.output_dir), "-DRAILWAY_FETCH_GTEST=ON"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ CMake configuration failed: {e}")
            return False

        # Build tests
        print("🔨 Building tests...")
        try:
            subprocess.run(["cmake", "--build", "."], cwd=self.output_dir, check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Build failed: {e}")
            return False

        # Run tests
        print("🧪 Running tests...")
        try:
            result = subprocess.run(["ctest", "--output-on-failure"], cwd=self.output_dir, capture_output=True, text=True)
            # For simplicity, assume success if no exception
            test_results = [{"passed": result.returncode == 0, "output": result.stdout + result.stderr}]
        except subprocess.CalledProcessError as e:
            print(f"❌ Tests failed: {e}")
            test_results = []

        if not test_results or not test_results[0]["passed"]:
            print("❌ No tests were executed or tests failed")
            return False

        print("✅ All tests passed!")
        print(f"📄 Test Output:\n{test_results[0]['output']}")

        return True


def main():
    """Main entry point for the AI Test Runner."""
    parser = argparse.ArgumentParser(
        description="AI Test Runner - Compiles, executes, and provides coverage for AI-generated C/C++ unit tests"
    )
    parser.add_argument(
        "repo_path",
        help="Path to the repository containing tests"
    )
    parser.add_argument(
        "--output-dir",
        default="build",
        help="Output directory name under <repo>/tests/ (default: build -> tests/build)"
    )
    parser.add_argument(
        "--language",
        choices=["c", "cpp", "auto"],
        default="auto",
        help="Programming language (default: auto-detect)"
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0"
    )

    args = parser.parse_args()

    # MANDATORY HUMAN REVIEW GATE — DO NOT BYPASS
    _enforce_manual_review_gate(Path(args.repo_path).resolve())

    # Create and run the test runner
    runner = AITestRunner(args.repo_path, args.output_dir, args.language)
    success = runner.run()

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
