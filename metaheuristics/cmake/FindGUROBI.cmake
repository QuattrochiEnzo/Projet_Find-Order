# FindGUROBI.cmake

# Sorties:
#   GUROBI_FOUND
#   GUROBI_INCLUDE_DIRS
#   GUROBI_LIBRARY
#   GUROBI_CXX_LIBRARY
#   GUROBI_CXX_DEBUG_LIBRARY

set(_GUROBI_HINTS "")

if(DEFINED GUROBI_DIR)
    list(APPEND _GUROBI_HINTS "${GUROBI_DIR}")
endif()
if(DEFINED ENV{GUROBI_HOME})
    list(APPEND _GUROBI_HINTS "$ENV{GUROBI_HOME}")
endif()
if(DEFINED ENV{GUROBI_DIR})
    list(APPEND _GUROBI_HINTS "$ENV{GUROBI_DIR}")
endif()
if(DEFINED GUROBI_HOME)
    list(APPEND _GUROBI_HINTS "${GUROBI_HOME}")
endif()


if(WIN32)
    list(APPEND _GUROBI_HINTS
        "D:/Gurobi/win64"
        "C:/gurobi/win64"
        "C:/Gurobi/win64"
        "C:/Program Files/gurobi/win64"
    )
endif()

if(NOT GUROBI_INCLUDE_DIRS)
    find_path(GUROBI_INCLUDE_DIRS
        NAMES gurobi_c.h
        HINTS ${_GUROBI_HINTS}
        PATH_SUFFIXES include)
endif()

if(NOT GUROBI_LIBRARY)
    find_library(GUROBI_LIBRARY
        NAMES gurobi gurobi130 gurobi120 gurobi110 gurobi100
        HINTS ${_GUROBI_HINTS}
        PATH_SUFFIXES lib)
endif()

if(NOT GUROBI_CXX_LIBRARY)
    if(MSVC)
        # On tente plusieurs annees, ca reste compatible
        find_library(GUROBI_CXX_LIBRARY
            NAMES
                gurobi_c++md2022 gurobi_c++md2019 gurobi_c++md2017
                gurobi_c++mt2022 gurobi_c++mt2019 gurobi_c++mt2017
                gurobi_c++
            HINTS ${_GUROBI_HINTS}
            PATH_SUFFIXES lib)
    else()
        find_library(GUROBI_CXX_LIBRARY
            NAMES gurobi_c++
            HINTS ${_GUROBI_HINTS}
            PATH_SUFFIXES lib)
    endif()
endif()

if(NOT GUROBI_CXX_DEBUG_LIBRARY)
    if(MSVC)
        find_library(GUROBI_CXX_DEBUG_LIBRARY
            NAMES
                gurobi_c++mdd2022 gurobi_c++mdd2019 gurobi_c++mdd2017
                gurobi_c++mtd2022 gurobi_c++mtd2019 gurobi_c++mtd2017
                gurobi_c++
            HINTS ${_GUROBI_HINTS}
            PATH_SUFFIXES lib)
    else()
        set(GUROBI_CXX_DEBUG_LIBRARY ${GUROBI_CXX_LIBRARY})
    endif()
endif()

include(FindPackageHandleStandardArgs)


find_package_handle_standard_args(GUROBI DEFAULT_MSG
    GUROBI_INCLUDE_DIRS GUROBI_LIBRARY GUROBI_CXX_LIBRARY)

mark_as_advanced(GUROBI_INCLUDE_DIRS GUROBI_LIBRARY GUROBI_CXX_LIBRARY GUROBI_CXX_DEBUG_LIBRARY)
