from yaml import safe_load
from pathlib import Path
import re
from jinja2 import Environment, FileSystemLoader

def normalize_port_name(port_name):
    """Normalize port name to be valid for Graphviz (cannot start with digit)"""
    normalized = port_name.replace('-', '_').replace(' ', '_').replace('.', '_')
    # If port name starts with a digit, prefix with 'port_'
    if normalized and normalized[0].isdigit():
        normalized = 'port_' + normalized
    return normalized

def parse_connections(connection_string, devices):
    """Parse connection string into structured connection data with shorthand support"""
    connections = []
    used_ports = {}  # Track used ports per device to avoid conflicts
    
    # Clean up the connection string and split by whitespace
    # Then find -> patterns
    tokens = connection_string.split()
    
    i = 0
    while i < len(tokens):
        if i + 2 < len(tokens) and tokens[i + 1] == '->':
            source_part = tokens[i]
            target_part = tokens[i + 2]
            
            # Extract source device and port
            if ':' in source_part:
                source_device, source_port = source_part.split(':', 1)
                source_port = normalize_port_name(source_port)
                # Resolve to actual port name with prefix (is_source=True)
                if source_device in devices:
                    source_port = resolve_port_name(devices[source_device], source_port, is_source=True, device_id=source_device, used_ports=used_ports)
            else:
                source_device = source_part
                source_port = None
            
            # Extract target device and port  
            if ':' in target_part:
                target_device, target_port = target_part.split(':', 1)
                target_port = normalize_port_name(target_port)
                # Resolve to actual port name with prefix (is_source=False)
                if target_device in devices:
                    target_port = resolve_port_name(devices[target_device], target_port, is_source=False, device_id=target_device, used_ports=used_ports)
            else:
                target_device = target_part
                target_port = None
                
                # SHORTHAND SUPPORT: If no target port specified, use the device's power port
                if target_device in devices:
                    target_device_info = devices[target_device]
                    if 'power' in target_device_info and target_device_info['power'] != 'psu':
                        # Use the power connection as the target port
                        power_port = normalize_port_name(target_device_info['power'])
                        target_port = power_port
                    else:
                        # If no power port, try to find the first input port
                        for key, value in target_device_info.items():
                            if key.startswith('in-'):
                                target_port = f"in_{normalize_port_name(value)}"
                                break
                
            # Determine connection type and color
            if source_port and 'usb' in source_port.lower():
                label = "USB"
                color = "#0074D9"
            elif source_port and 'sdi' in source_port.lower():
                label = "SDI"
                color = "#00FF00"
            elif source_port and 'hdmi' in source_port.lower():
                label = "HDMI"
                color = "#8E44AD"
            elif source_port and 'eth' in source_port.lower():
                label = "ETH"
                color = "#E67E22"
            elif source_port and 'rj45' in source_port.lower():
                label = "RJ45"
                color = "#4D4D4D"
            elif target_device == 'headphones' or (target_port and 'audio' in target_port.lower()):
                label = "Audio"
                color = "#A21E7D"
            else:
                # label = "Connection"
                label = ""
                color = "black"
            
            # Determine if connection should have arrow (directional)
            # port-x connections are bidirectional (no arrow)
            # in-x to out-x connections are directional (with arrow)
            # eth and rj45 connections are bidirectional
            # usb connections are directional
            is_bidirectional = False
            
            # Check connection type for bidirectional behavior
            if source_port and ('eth' in source_port.lower() or 'rj45' in source_port.lower()):
                is_bidirectional = True
            
            # Check if source port is a port-x (bidirectional)
            if source_device in devices:
                source_device_info = devices[source_device]
                for key, value in source_device_info.items():
                    if key.startswith('port-') and normalize_port_name(value) == source_port:
                        is_bidirectional = True
                        break
            
            connections.append({
                'source': source_device,
                'source_port': source_port,
                'target': target_device,
                'target_port': target_port,
                'label': label,
                'color': color,
                'is_bidirectional': is_bidirectional
            })
            
            i += 3  # Skip the tokens we just processed
        else:
            i += 1
    
    return connections

def get_max_cols(device_info):
    """Calculate maximum columns needed for a device table"""
    max_cols = 2  # Default minimum
    
    # Check for long port names that need more space
    for key, value in device_info.items():
        if key.startswith('port-') and isinstance(value, str):
            if 'x' in value:
                # For "24x RJ45" format, we want 2 columns for the port table
                return 2
        elif key.startswith('out-') and isinstance(value, str):
            # If output port name is long, increase columns
            if len(value) > 8:
                max_cols = 3
    
    # Check if any port values are long
    for key, value in device_info.items():
        if isinstance(value, str) and len(value) > 10:
            max_cols = max(max_cols, 3)
    
    return max_cols

def process_device_data(devices):
    """Process device data to make it template-friendly"""
    processed = {}
    
    for device_id, device_list in devices.items():
        if isinstance(device_list, list):
            # Convert list format to dict
            device_dict = {}
            for item in device_list:
                if isinstance(item, dict):
                    device_dict.update(item)
                else:
                    # Handle string items
                    device_dict[f'item_{len(device_dict)}'] = item
            processed[device_id] = device_dict
        else:
            processed[device_id] = device_list
    
    return processed

def get_device_color(device_info):
    """Get device color based on type"""
    device_type = device_info.get('type', 'pc')
    
    color_map = {
        'pc': '#FFD700',      # Yellow (existing)
        'network': '#60A917',  # Green
        'converter': '#1BA1E2', # Blue
        'venue': "#AFAA8B"     # Default to yellow for venue
    }
    
    return color_map.get(device_type, '#FFD700')  # Default to yellow

def resolve_port_name(device_info, port_name, is_source=True, device_id=None, used_ports=None):
    """Resolve a port name to its actual port identifier in the device"""
    if not port_name:
        return None
    
    if used_ports is None:
        used_ports = {}
    
    if device_id not in used_ports:
        used_ports[device_id] = set()
    
    # For sources (left side of ->), prefer output ports
    # For targets (right side of ->), prefer input ports
    
    if is_source:
        # Check output ports first for source connections
        for key, value in device_info.items():
            if key.startswith('out-') and normalize_port_name(value) == port_name:
                port_id = f"out_{normalize_port_name(value)}"
                if port_id not in used_ports[device_id]:
                    used_ports[device_id].add(port_id)
                    return port_id
        
        # Then check input ports
        for key, value in device_info.items():
            if key.startswith('in-') and normalize_port_name(value) == port_name:
                port_id = f"in_{normalize_port_name(value)}"
                if port_id not in used_ports[device_id]:
                    used_ports[device_id].add(port_id)
                    return port_id
    else:
        # Check input ports first for target connections
        for key, value in device_info.items():
            if key.startswith('in-') and normalize_port_name(value) == port_name:
                port_id = f"in_{normalize_port_name(value)}"
                if port_id not in used_ports[device_id]:
                    used_ports[device_id].add(port_id)
                    return port_id
        
        # Then check output ports
        for key, value in device_info.items():
            if key.startswith('out-') and normalize_port_name(value) == port_name:
                port_id = f"out_{normalize_port_name(value)}"
                if port_id not in used_ports[device_id]:
                    used_ports[device_id].add(port_id)
                    return port_id
    
    # Check for numbered ports (e.g., eth -> eth_1, eth_2, etc.)
    for key, value in device_info.items():
        if key.startswith('port-') and 'x' in value:
            count, port_type = value.split('x')
            port_type_normalized = port_type.strip().lower().replace(' ', '').replace('-', '')
            if port_name.lower() == port_type_normalized:
                # Find the first available port of this type
                count_int = int(count.strip())
                for i in range(1, count_int + 1):
                    port_id = f"{port_type_normalized}_{i}"
                    if port_id not in used_ports[device_id]:
                        used_ports[device_id].add(port_id)
                        return port_id
                # If all ports are used, return None to indicate no available ports
                return None
    
    # For regular ports and power ports, check if available
    if port_name not in used_ports[device_id]:
        used_ports[device_id].add(port_name)
        return port_name
    
    return None

def main():
    # Load data
    dfile = Path(__file__).parent / "data.yaml"
    data = safe_load(dfile.read_text(encoding="utf-8"))
    
    # Extract connections
    connection_string = data.pop("connection", None)
    if not connection_string:
        exit("No connections found in data.yaml")
    
    # Process devices and connections
    devices = process_device_data(data)
    connections = parse_connections(connection_string, devices)
    
    # Setup Jinja2 environment
    env = Environment(loader=FileSystemLoader('.'))
    env.globals['get_max_cols'] = get_max_cols
    env.globals['normalize_port_name'] = normalize_port_name
    env.globals['get_device_color'] = get_device_color
    template = env.get_template('graphviz_template.j2')
    
    # Render the template
    output = template.render(devices=devices, connections=connections)
    
    # Write output
    output_file = Path(__file__).parent / "generated.gv"
    output_file.write_text(output, encoding="utf-8")
    
    print(f"Generated Graphviz file: {output_file}")
    print("✓ Successfully converted data.yaml to Graphviz format with shorthand connection support")

if __name__ == "__main__":
    main()