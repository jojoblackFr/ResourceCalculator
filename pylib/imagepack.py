from PIL import Image  # type: ignore
from typing import List, Dict, Tuple, TypedDict
import json
import math
import os
import re
import shutil
import subprocess

from pylib.producer import Producer, MultiFile, SingleFile, GenericProducer
from pylib.filehash import getfilehash

################################################################################
# ImagePackOutputFiles
#
# A TypedDict representing the output files structure for the producers that
# create the packed image.
################################################################################
class ImagePackOutputFiles(TypedDict):
    image_file: str
    image_layout_file: str


################################################################################
# item_image_producers
#
# Creates the producers for packing images into a single file and the
# producers that take that compressed file and compress it into the final image.
################################################################################
def item_image_producers(calculator_dir_regex: str) -> List[GenericProducer]:

    copy_hashed_image: Producer[SingleFile, SingleHashedFile] = Producer(
        input_path_patterns={
            "file": r"^cache/(?P<calculator_dir>{calculator_dir_regex})/compressed_packed_image\.png$".format(
                calculator_dir_regex=calculator_dir_regex,
            ),
        },
        paths=hash_and_copy_file_paths,
        function=hash_and_copy_file,
        categories=["landing"],
    )

    return [
        # Pack Image
        Producer(
            input_path_patterns={
                "files": [r"^resource_lists/(?P<calculator_dir>{calculator_dir_regex})/items/.*$".format(
                    calculator_dir_regex=calculator_dir_regex
                )],
            },
            paths=image_pack_paths,
            function=image_pack_function,
            categories=["image"]
        ),

        # Compress Image
        Producer(
            input_path_patterns={
                "file": r"^cache/(?P<calculator_dir>{calculator_dir_regex})/packed_image\.png$".format(
                    calculator_dir_regex=calculator_dir_regex
                ),
            },
            paths=image_compress_paths,
            function=image_compress_function,
            categories=["image", "compress", "imagecompress"]
        ),
        copy_hashed_image
    ]


################################################################################
# image_pack_paths
#
# The input and output paths generation function for packing images into a
# single tiled image.
################################################################################
def image_pack_paths(input_files: MultiFile, categories: Dict[str, str]) -> Tuple[MultiFile, ImagePackOutputFiles]:
    calculator_page = categories["calculator_dir"]

    calculator_imagefile = os.path.join("cache", calculator_page, "packed_image.png")
    calculator_image_layout = os.path.join("cache", calculator_page, "packed_image_layout.json")

    return (
        input_files,
        {
            "image_file": calculator_imagefile,
            "image_layout_file": calculator_image_layout,
        }
    )


################################################################################
# image_pack_function
#
# This function will take all the files within the resource_lists/[list]/items
# and create a single packed image of them. Then return the coordinates so that
# css can be written to load all of the images from the same file instead of
# making a large number of get requests for the file
################################################################################
def image_pack_function(input_files: MultiFile, output_files: ImagePackOutputFiles) -> None:
    output_image_path: str = output_files["image_file"]
    output_data_path: str = output_files["image_layout_file"]
    input_image_files: List[str] = input_files["files"]

    image_coordinates: Dict[str, Tuple[int, int]] = {}

    # Build tuple of simple names to filepaths
    images: List[Tuple[str, str]] = []
    for file in input_image_files:
        images.append((
            os.path.splitext(os.path.basename(file))[0],
            file
        ))

    # Open first image to get a standard
    first_image: Image.Image = Image.open(images[0][1])
    standard_width: int
    standard_height: int
    standard_width, standard_height = first_image.size
    standard_image_reference: str = images[0][1]

    # Sort the images, this is probably not necessary but will allow for
    # differences between files to be noticed with less noise of random shifting of squares
    images.sort(key=lambda x: x[0])

    # Use our special math function to determine what the number of columns
    # should be for the final packed image.
    # Programmers note: This was a lot of fun to figure out and derived strangely
    columns: int = math.ceil(math.sqrt(standard_height * len(images) / standard_width))
    result_width: int = standard_width * columns
    result_height: int = standard_height * math.ceil((len(images) / columns))

    # Determine where each image should go
    for index, (name, image) in enumerate(images):
        x_coordinate = (index % columns) * standard_width
        y_coordinate = math.floor(index / columns) * standard_height
        image_coordinates[name] = (x_coordinate, y_coordinate)

    # Create the new packed image file and all the coordinates of the images
    result = Image.new('RGBA', (result_width, result_height))
    for image_name, image_path in images:
        image_object = Image.open(image_path)
        width, height = image_object.size

        if (standard_width != width or standard_height != height):
            print("ERROR: All resource list item images for a single calculator must be the same size")
            print("       " + image_path + " and " + standard_image_reference + " are not the same size")

        x_coordinate, y_coordinate = image_coordinates[image_name]
        result.paste(im=image_object, box=(x_coordinate, y_coordinate))
    result.save(output_image_path)

    # Write the metadata for the packed image that will be used for later phases
    with open(output_data_path, 'w') as f:
        json.dump({
            "standard_width": standard_width,
            "standard_height": standard_height,
            "image_coordinates": image_coordinates
        }, f)


################################################################################
# image_compress_paths
#
# The input and output paths generation function for compressing image files
# into the output directory.
################################################################################
def image_compress_paths(input_files: SingleFile, categories: Dict[str, str]) -> Tuple[SingleFile, SingleFile]:
    calculator_page = categories["calculator_dir"]

    output_calculator_imagefile = os.path.join("cache", calculator_page, "compressed_packed_image.png")

    return (
        input_files,
        {
            "file": output_calculator_imagefile
        }
    )

################################################################################
# image_compress_function
#
# The function that generates a compressed png image given an input and
# output file.
################################################################################
def image_compress_function(input_files: SingleFile, output_files: SingleFile) -> None:
    input_file = input_files["file"]
    output_file = output_files["file"]

    # Copy the file
    shutil.copyfile(input_file, output_file)

    try:
        subprocess.run(["pngquant", "--force", "--ext", ".png", "256", "--nofs", output_file])
    except OSError as e:
        print("WARNING: PNG Compression Failed. This is non-critical in a development environment")
        print("        ", e)


################################################################################
# image_copy_function
#
# The function used if image compression is skipped. Instead of compressing
# a file it is instead just copied over to the destination.
#################################################################################
def image_copy_function(input_file: str, match: "re.Match[str]", output_files: List[str]) -> None:
    # Sanity check that there is only one output
    if len(output_files) != 1:
        raise ValueError("Must copy " + input_file + " to only one location not" + str(output_files))
    output_file = output_files[0]

    # Copy the file
    shutil.copyfile(input_file, output_file)


class SingleHashedFile(TypedDict):
    file: str
    filemetadata: str


################################################################################
# hash_and_copy_file
#
# Copies a file with a dynamic output and saves a file with that dynamic output
# in a fixed location for later lookups
################################################################################
def hash_and_copy_file(
    input_files: SingleFile,
    output_files: SingleHashedFile
) -> None:
    input_file: str = input_files["file"]
    output_file: str = output_files["file"]

    output_metadata_file: str = output_files["filemetadata"]

    # A GIANT HACK, but soon it wont be because we will be getting rid of the "paths" functions eventually so it is ok
    file_hash = getfilehash(input_file)
    base, extention = os.path.splitext(output_file)
    output_file = base + "-" + file_hash + extention

    # Copy the file
    shutil.copyfile(input_file, output_file)

    # Write the hashed file name to a known location
    with open(output_metadata_file, 'w') as f:
        json.dump({
            "filename": output_file
        }, f)


################################################################################
# logo_copy_paths
#
# The input and output paths generation function for copying icon files into the
# output directory.
################################################################################
def hash_and_copy_file_paths(
    input_files: SingleFile,
    categories: Dict[str, str]
) -> Tuple[SingleFile, SingleHashedFile]:

    calculator_name = categories["calculator_dir"]

    # A GIANT HACK but we will be getting rid of the "paths" functions eventually so it is ok
    # We are doing this because this is a hack on a hack, and the original hack
    # where we create the file hash within the "paths" function wont work on
    # any file that does not exist at the start of generation. Nothing relies
    # on the image output itself, so we are just lying about what the output
    # filename is here so that we dont need to generate the hash here to
    # determine the output filename. The only file that is depended on is the
    # metadata which contains the real filepath, not the fake one.
    #
    # A new version of the scheduler will be getting rid of the "paths" function
    # concept entirely rendering this hack,and the hack it is hacking, obsolete
    # and will make this common process of hashed filenames much more trivial
    # to implement.
    new_filename = calculator_name + ".png"

    return (
        input_files,
        {
            "file": os.path.join("output", calculator_name, new_filename),
            "filemetadata": os.path.join("cache", calculator_name, "compressed_packed_image.json"),
        }
    )