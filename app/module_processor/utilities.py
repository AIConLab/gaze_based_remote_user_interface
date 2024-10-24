import cv2
import numpy as np
import cairosvg
import io
from PIL import Image

class AprilTagRenderer:
    """
    Renders April tags on a video object to create bounds for a PupilTag surface
    """
    def __init__(self, 
                 upper_left_corner_tag_path: str,
                 lower_left_corner_tag_path: str,
                 upper_right_corner_tag_path: str,
                 lower_right_corner_tag_path: str,
                 scale: float = 1/8
                ):

        self.upper_left_corner_tag = None
        self.lower_left_corner_tag = None

        self.upper_right_corner_tag = None
        self.lower_right_corner_tag = None

        self.__load_tags(upper_left_corner_tag_path=upper_left_corner_tag_path,
                         lower_left_corner_tag_path=lower_left_corner_tag_path,
                         upper_right_corner_tag_path=upper_right_corner_tag_path,
                         lower_right_corner_tag_path=lower_right_corner_tag_path)


        self.latest_frame = None
        self.scale = scale

    def set_latest_frame(self, frame):
        self.latest_frame = frame

    def get_latest_frame(self):
        """
        Function to retrun latest frame with April tags rendered
        """
        frame = self.latest_frame
        height, width = frame.shape[:2]
        
        tag_size = int(min(width, height) * self.scale)

        # Render tags
        frame = self.__render_tag(frame, self.upper_left_corner_tag, (0, 0), tag_size)
        frame = self.__render_tag(frame, self.lower_left_corner_tag, (0, height - tag_size), tag_size)
        
        frame = self.__render_tag(frame, self.upper_right_corner_tag, (width - tag_size, 0), tag_size)

        frame = self.__render_tag(frame, self.lower_right_corner_tag, (width - tag_size, height - tag_size), tag_size)  
    
        
        return frame

    
    def __render_tag(self, frame, tag, position, size):
        tag_resized = cv2.resize(tag, (size, size), interpolation=cv2.INTER_AREA)
        
        x, y = position
        frame[y:y+size, x:x+size] = tag_resized
        return frame

    def __load_tags(self, upper_left_corner_tag_path, lower_left_corner_tag_path, upper_right_corner_tag_path, lower_right_corner_tag_path):

        self.upper_left_corner_tag = self.__svg_to_numpy(upper_left_corner_tag_path)
        self.lower_left_corner_tag = self.__svg_to_numpy(lower_left_corner_tag_path)

        self.upper_right_corner_tag = self.__svg_to_numpy(upper_right_corner_tag_path)
        self.lower_right_corner_tag = self.__svg_to_numpy(lower_right_corner_tag_path)

    def __svg_to_numpy(self, svg_path):

        with open(svg_path, 'rb') as svg_file:
            svg_data = svg_file.read()
        png_data = cairosvg.svg2png(bytestring=svg_data)
        img = Image.open(io.BytesIO(png_data))
        img = img.convert('RGB')
        return np.array(img)
