# visualizer_service.py
import ast
import os
from typing import Dict, List, Set, Tuple
from dataclasses import dataclass
import graphviz
import argparse
from pathlib import Path

@dataclass
class PubSubInfo:
    publishes: Set[str]
    subscribes: Set[str]

class MessageBrokerVisualizer:
    def __init__(self):
        self.node_connections: Dict[str, PubSubInfo] = {}
        
    def parse_file(self, file_path: str):
        """Parse a Python file to extract pub/sub information."""
        with open(file_path, 'r') as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                print(f"Syntax error in file: {file_path}")
                return
            
        # Find all class definitions
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._analyze_class(node, file_path)
                
    def _analyze_class(self, class_node: ast.ClassDef, file_path: str):
        """Analyze a class definition for pub/sub patterns."""
        # Get container name from directory structure
        container_name = Path(file_path).parent.name
        class_name = f"{container_name}/{class_node.name}"
        publishes = set()
        subscribes = set()
        
        for node in ast.walk(class_node):
            # Look for method calls
            if isinstance(node, ast.Call):
                # Check if it's a message_broker publish or subscribe call
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == 'publish':
                        # Extract topic from first argument
                        if len(node.args) > 0 and isinstance(node.args[0], ast.Constant):
                            publishes.add(node.args[0].value)
                    elif node.func.attr == 'subscribe':
                        # Extract topic from first argument
                        if len(node.args) > 0 and isinstance(node.args[0], ast.Constant):
                            subscribes.add(node.args[0].value)
        
        if publishes or subscribes:  # Only add nodes that actually communicate
            self.node_connections[class_name] = PubSubInfo(publishes, subscribes)
    
    def generate_graph(self, output_path: str, format: str = 'png'):
        """Generate a Graphviz visualization of the pub/sub system."""
        dot = graphviz.Digraph(comment='Message Broker Pub/Sub Visualization')
        dot.attr(rankdir='LR')
        
        # Add nodes with container grouping
        containers = {}
        for node_name in self.node_connections:
            container, class_name = node_name.split('/')
            if container not in containers:
                containers[container] = set()
            containers[container].add(class_name)
            
        # Create container clusters
        for container, classes in containers.items():
            with dot.subgraph(name=f'cluster_{container}') as c:
                c.attr(label=container, style='rounded', bgcolor='lightgrey')
                for class_name in classes:
                    c.node(f"{container}/{class_name}", class_name)
        
        # Add edges with topic labels
        added_connections = set()
        for publisher, info in self.node_connections.items():
            for topic in info.publishes:
                # Find subscribers for this topic
                for subscriber, sub_info in self.node_connections.items():
                    if topic in sub_info.subscribes:
                        connection_id = f'{publisher}-{topic}-{subscriber}'
                        if connection_id not in added_connections:
                            dot.edge(publisher, subscriber, label=topic)
                            added_connections.add(connection_id)
        
        # Save the visualization
        dot.render(output_path, format=format, cleanup=True)
        print(f"Generated visualization: {output_path}.{format}")

def main():
    parser = argparse.ArgumentParser(description='Generate pub/sub visualization')
    parser.add_argument('--src_dir', type=str, required=True, help='Source directory to analyze')
    parser.add_argument('--output', type=str, default='pubsub_visualization', help='Output file path (without extension)')
    parser.add_argument('--format', type=str, default='png', choices=['png', 'pdf'], help='Output format')
    
    args = parser.parse_args()
    
    visualizer = MessageBrokerVisualizer()
    
    # Process all Python files in the directory
    for root, _, files in os.walk(args.src_dir):
        for file in files:
            if file.endswith('.py'):
                visualizer.parse_file(os.path.join(root, file))
    
    visualizer.generate_graph(args.output, args.format)

if __name__ == '__main__':
    main()