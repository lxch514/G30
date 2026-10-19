#----------------------------------------------------------------
# Generated CMake target import file for configuration "None".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "uvc" for configuration "None"
set_property(TARGET uvc APPEND PROPERTY IMPORTED_CONFIGURATIONS NONE)
set_target_properties(uvc PROPERTIES
  IMPORTED_LINK_INTERFACE_LIBRARIES_NONE "-L/build/libjpeg-turbo-vr67J4/libjpeg-turbo-2.0.3/obj-aarch64-linux-gnu/lib/aarch64-linux-gnu;-ljpeg;usb-1.0"
  IMPORTED_LOCATION_NONE "/usr/lib/aarch64-linux-gnu/libuvc.so.0.0.6"
  IMPORTED_SONAME_NONE "libuvc.so.0"
  )

list(APPEND _IMPORT_CHECK_TARGETS uvc )
list(APPEND _IMPORT_CHECK_FILES_FOR_uvc "/usr/lib/aarch64-linux-gnu/libuvc.so.0.0.6" )

# Import target "uvc_static" for configuration "None"
set_property(TARGET uvc_static APPEND PROPERTY IMPORTED_CONFIGURATIONS NONE)
set_target_properties(uvc_static PROPERTIES
  IMPORTED_LINK_INTERFACE_LANGUAGES_NONE "C"
  IMPORTED_LOCATION_NONE "/usr/lib/aarch64-linux-gnu/libuvc.a"
  )

list(APPEND _IMPORT_CHECK_TARGETS uvc_static )
list(APPEND _IMPORT_CHECK_FILES_FOR_uvc_static "/usr/lib/aarch64-linux-gnu/libuvc.a" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
