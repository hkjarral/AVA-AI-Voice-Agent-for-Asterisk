#!/usr/bin/env python3
"""
Test Call Log Capture Script

This script captures structured JSON logs from both AI engine and local AI server
during test calls for comprehensive troubleshooting analysis.

Usage:
    python scripts/capture_test_logs.py --duration 40 --output test-call-logs.json
"""

import asyncio
import json
import argparse
import subprocess
import time
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any
import os

class TestLogCapture:
    def __init__(self, duration: int = 40, output_file: str = None):
        self.duration = duration
        self.output_file = output_file or f"test-call-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        self.captured_logs = []
        self.processes = []
        self.start_time = None
        self.end_time = None
        
    def start_capture(self):
        """Start capturing logs from both containers."""
        print(f"🔍 Starting log capture for {self.duration} seconds...")
        print(f"📁 Output file: {self.output_file}")
        
        self.start_time = datetime.now()
        
        # Local log capture commands (no hardcoded remote host)
        compose = ["docker-compose", "-p", "asterisk-ai-voice-agent"]
        try:
            probe = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                compose = ["docker", "compose", "-p", "asterisk-ai-voice-agent"]
        except Exception:
            pass

        ai_engine_cmd = compose + ["logs", "-f", "ai_engine"]
        local_ai_cmd = compose + ["logs", "-f", "local_ai_server"]
        asterisk_cmd = ["tail", "-f", "/var/log/asterisk/full"]
        
        try:
            # Start AI Engine log capture
            ai_engine_proc = subprocess.Popen(
                ai_engine_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            self.processes.append(("ai_engine", ai_engine_proc))
            
            # Start Local AI Server log capture
            local_ai_proc = subprocess.Popen(
                local_ai_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            self.processes.append(("local_ai_server", local_ai_proc))
            
            # Start Asterisk log capture
            asterisk_proc = subprocess.Popen(
                asterisk_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            self.processes.append(("asterisk", asterisk_proc))
            
            print("✅ Log capture started for ai_engine, local_ai_server, and asterisk")
            return True
            
        except Exception as e:
            print(f"❌ Failed to start log capture: {e}")
            return False
    
    def capture_logs(self):
        """Capture logs for the specified duration."""
        if not self.start_capture():
            return False
            
        print(f"⏱️  Capturing logs for {self.duration} seconds...")
        print("📞 Make your test call now!")
        
        # Set up signal handler for graceful shutdown
        def signal_handler(signum, frame):
            print("\n🛑 Stopping log capture...")
            self.stop_capture()
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
        
        try:
            # Capture logs for the specified duration
            end_time = time.time() + self.duration
            
            while time.time() < end_time:
                for service_name, proc in self.processes:
                    if proc.poll() is None:  # Process is still running
                        try:
                            # Read available output
                            line = proc.stdout.readline()
                            if line:
                                self._process_log_line(service_name, line.strip())
                        except Exception as e:
                            print(f"⚠️  Error reading from {service_name}: {e}")
                
                time.sleep(0.1)  # Small delay to prevent excessive CPU usage
                
        except KeyboardInterrupt:
            print("\n🛑 Log capture interrupted by user")
        finally:
            self.stop_capture()
            
        return True
    
    def _process_log_line(self, service_name: str, line: str):
        """Process a single log line and add to captured logs."""
        try:
            # Handle Asterisk logs differently
            if service_name == "asterisk":
                # Parse Asterisk log format: [timestamp] LEVEL[pid][context] module: message
                log_entry = {
                    'capture_timestamp': datetime.now().isoformat(),
                    'source_service': service_name,
                    'raw_message': line,
                    'level': 'info',
                    'event': 'asterisk_log'
                }
                
                # Try to extract timestamp and level from Asterisk log
                if line.startswith('[') and ']' in line:
                    try:
                        timestamp_end = line.find(']')
                        timestamp = line[1:timestamp_end]
                        remaining = line[timestamp_end + 1:].strip()
                        
                        if 'VERBOSE' in remaining:
                            log_entry['level'] = 'debug'
                        elif 'WARNING' in remaining:
                            log_entry['level'] = 'warning'
                        elif 'ERROR' in remaining:
                            log_entry['level'] = 'error'
                        elif 'DEBUG' in remaining:
                            log_entry['level'] = 'debug'
                        elif 'NOTICE' in remaining:
                            log_entry['level'] = 'info'
                        
                        log_entry['asterisk_timestamp'] = timestamp
                    except:
                        pass  # Keep default values if parsing fails
                
                self.captured_logs.append(log_entry)
                
            else:
                # Try to parse as JSON (structured logs from containers)
                if line.startswith('{'):
                    log_entry = json.loads(line)
                    log_entry['capture_timestamp'] = datetime.now().isoformat()
                    log_entry['source_service'] = service_name
                    self.captured_logs.append(log_entry)
                else:
                    # Handle non-JSON logs (like Docker Compose headers)
                    log_entry = {
                        'capture_timestamp': datetime.now().isoformat(),
                        'source_service': service_name,
                        'raw_message': line,
                        'level': 'info',
                        'event': 'raw_log_line'
                    }
                    self.captured_logs.append(log_entry)
                
        except json.JSONDecodeError:
            # Handle non-JSON logs
            log_entry = {
                'capture_timestamp': datetime.now().isoformat(),
                'source_service': service_name,
                'raw_message': line,
                'level': 'info',
                'event': 'raw_log_line'
            }
            self.captured_logs.append(log_entry)
    
    def stop_capture(self):
        """Stop all log capture processes."""
        self.end_time = datetime.now()
        
        for service_name, proc in self.processes:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                print(f"✅ Stopped {service_name} log capture")
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"⚠️  Force killed {service_name} log capture")
            except Exception as e:
                print(f"⚠️  Error stopping {service_name}: {e}")
        
        self.processes.clear()
    
    def save_logs(self):
        """Save captured logs to JSON file and generate framework analysis."""
        if not self.captured_logs:
            print("⚠️  No logs captured")
            return False
            
        # Create summary metadata
        summary = {
            'capture_session': {
                'start_time': self.start_time.isoformat() if self.start_time else None,
                'end_time': self.end_time.isoformat() if self.end_time else None,
                'duration_seconds': self.duration,
                'total_log_entries': len(self.captured_logs),
                'services_captured': list(set(log.get('source_service') for log in self.captured_logs))
            },
            'logs': self.captured_logs
        }
        
        try:
            # Save JSON logs
            with open(self.output_file, 'w') as f:
                json.dump(summary, f, indent=2)
            
            print(f"✅ Logs saved to {self.output_file}")
            print(f"📊 Captured {len(self.captured_logs)} log entries")
            
            # Generate and save framework analysis
            framework_file = self.output_file.replace('.json', '.md')
            self._generate_framework_analysis(summary, framework_file)
            
            # Print summary statistics
            self._print_summary()
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to save logs: {e}")
            return False
    
    def _generate_framework_analysis(self, summary, framework_file):
        """Generate call framework analysis markdown file."""
        try:
            # Import the analyzer
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.abspath(__file__)))
            from analyze_logs import LogAnalyzer
            
            # Create temporary log file for analysis
            temp_log_file = framework_file.replace('.md', '_temp.json')
            with open(temp_log_file, 'w') as f:
                json.dump(summary, f, indent=2)
            
            # Run analysis
            analyzer = LogAnalyzer(temp_log_file)
            if analyzer.load_logs():
                framework_content = analyzer._generate_call_framework()
                
                # Save framework analysis
                with open(framework_file, 'w') as f:
                    f.write(framework_content)
                
                print(f"📋 Framework analysis saved to {framework_file}")
                
                # Clean up temp file
                os.remove(temp_log_file)
                
            else:
                print("⚠️  Failed to generate framework analysis")
                
        except Exception as e:
            print(f"⚠️  Error generating framework analysis: {e}")
    
    def _print_summary(self):
        """Print summary statistics of captured logs."""
        if not self.captured_logs:
            return
            
        # Count by service
        service_counts = {}
        level_counts = {}
        event_counts = {}
        
        for log in self.captured_logs:
            service = log.get('source_service', 'unknown')
            level = log.get('level', 'unknown')
            event = log.get('event', 'unknown')
            
            service_counts[service] = service_counts.get(service, 0) + 1
            level_counts[level] = level_counts.get(level, 0) + 1
            event_counts[event] = event_counts.get(event, 0) + 1
        
        print("\n📊 Log Capture Summary:")
        print(f"   Total entries: {len(self.captured_logs)}")
        print(f"   Duration: {self.duration} seconds")
        print(f"   Services: {', '.join(service_counts.keys())}")
        print(f"   Levels: {', '.join(level_counts.keys())}")
        print(f"   Top events: {', '.join(sorted(event_counts.items(), key=lambda x: x[1], reverse=True)[:5])}")

def main():
    parser = argparse.ArgumentParser(description='Capture test call logs for troubleshooting')
    parser.add_argument('--duration', type=int, default=40, help='Capture duration in seconds (default: 40)')
    parser.add_argument('--output', type=str, help='Output JSON file path')
    parser.add_argument('--preview', action='store_true', help='Preview logs without saving')
    
    args = parser.parse_args()
    
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    if args.output:
        output_file = f"logs/{args.output}"
    else:
        output_file = f"logs/test-call-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    
    capture = TestLogCapture(duration=args.duration, output_file=output_file)
    
    try:
        if capture.capture_logs():
            if not args.preview:
                capture.save_logs()
            else:
                print("🔍 Preview mode - logs not saved")
        else:
            print("❌ Log capture failed")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Error during log capture: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
