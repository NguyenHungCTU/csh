# Generate postRoute RC scaling factors from golden and target SPEF files.
# Run this script inside Ostrich, not at the Innovus prompt.
#
#   ostrich -nowin
#   source gen_postroute_rc_factors.tcl

# -----------------------------------------------------------------------------
# User configuration
# -----------------------------------------------------------------------------

# RC corner names. The names are case-sensitive and must match the SPEF names.
set CORNERS {
    RC_WORST
    RC_BEST
}

# For every corner, the script expects:
#   ${GOLDEN_DIR}/fgotn_top_${corner}.spef.gz
#   ${TARGET_DIR}/fgotn_top_${corner}.spef.gz
set GOLDEN_DIR  "./golden"
set TARGET_DIR  "./target"
set FILE_PREFIX "fgotn_top"

# Factor placement in create_rc_corner/update_rc_corner:
#   low    -> {factor}
#   medium -> {1.0 factor}
#   high   -> {1.0 1.0 factor}
set POSTROUTE_EFFORT "medium"

set OUTPUT_TCL "./update_postRoute_RC_factors.tcl"
set OUTPUT_CSV "./postRoute_RC_factors.csv"

# -----------------------------------------------------------------------------
# Procedures
# -----------------------------------------------------------------------------

proc fail {message} {
    puts stderr "ERROR: $message"
    error $message
}

proc factor_vector {effort factor} {
    switch -- $effort {
        low {
            return [list $factor]
        }
        medium {
            return [list 1.0 $factor]
        }
        high {
            return [list 1.0 1.0 $factor]
        }
        default {
            fail "POSTROUTE_EFFORT must be low, medium, or high; got '$effort'"
        }
    }
}

proc check_numeric_factor {corner type value} {
    set value [string trim $value]
    if {![string is double -strict $value]} {
        fail "Invalid $type factor for corner '$corner': '$value'"
    }
    return $value
}

# -----------------------------------------------------------------------------
# Input validation
# -----------------------------------------------------------------------------

if {[llength $CORNERS] == 0} {
    fail "CORNERS is empty"
}

if {$POSTROUTE_EFFORT ni {low medium high}} {
    fail "POSTROUTE_EFFORT must be low, medium, or high"
}

array set seen_corner {}
set jobs {}
set missing_files {}

foreach corner $CORNERS {
    if {[info exists seen_corner($corner)]} {
        fail "Duplicated corner in CORNERS: $corner"
    }
    set seen_corner($corner) 1

    set filename "${FILE_PREFIX}_${corner}.spef.gz"
    set golden_spef [file join $GOLDEN_DIR $filename]
    set target_spef [file join $TARGET_DIR $filename]

    if {![file isfile $golden_spef]} {
        lappend missing_files $golden_spef
    }
    if {![file isfile $target_spef]} {
        lappend missing_files $target_spef
    }

    lappend jobs [list $corner $golden_spef $target_spef]
}

if {[llength $missing_files] > 0} {
    puts stderr "ERROR: Missing SPEF file(s):"
    foreach filename $missing_files {
        puts stderr "  $filename"
    }
    error "SPEF input validation failed"
}

# -----------------------------------------------------------------------------
# Correlation
# -----------------------------------------------------------------------------

array set cap_factor {}
array set res_factor {}
array set xcap_factor {}

set job_index 0
foreach job $jobs {
    foreach {corner golden_spef target_spef} $job break
    incr job_index

    # Use generated dataset names so unusual characters in corner names do not
    # affect Ostrich object names.
    set golden_set "GOLDEN_${job_index}"
    set target_set "TARGET_${job_index}"
    set plot_name "CORR_${job_index}"

    puts ""
    puts "============================================================"
    puts "Corner : $corner"
    puts "Golden : $golden_spef"
    puts "Target : $target_spef"
    puts "============================================================"

    read_spef -setname $golden_set -filename $golden_spef
    read_spef -setname $target_set -filename $target_spef

    foreach data_type {tcap res xcap} {
        build_plot \
            -plotname $plot_name \
            -golden $golden_set \
            -target $target_set \
            -datatype $data_type
    }

    set cap_factor($corner) [check_numeric_factor $corner tcap \
        [get_scale_factor -plotname $plot_name -datatype tcap -recommended]]

    set res_factor($corner) [check_numeric_factor $corner res \
        [get_scale_factor -plotname $plot_name -datatype res -recommended]]

    set xcap_factor($corner) [check_numeric_factor $corner xcap \
        [get_scale_factor -plotname $plot_name -datatype xcap -recommended]]
}

# -----------------------------------------------------------------------------
# Write results only after all corners complete successfully
# -----------------------------------------------------------------------------

foreach output_file [list $OUTPUT_TCL $OUTPUT_CSV] {
    set output_dir [file dirname $output_file]
    if {![file isdirectory $output_dir]} {
        file mkdir $output_dir
    }
}

set tcl_fp [open $OUTPUT_TCL w]
puts $tcl_fp "# Auto-generated postRoute RC scaling factors"
puts $tcl_fp "# Effort order: low, medium, high"
puts $tcl_fp "# Generated effort: $POSTROUTE_EFFORT"
puts $tcl_fp "# Clock factors are not modified by this file."
puts $tcl_fp ""

set csv_fp [open $OUTPUT_CSV w]
puts $csv_fp "corner,effort,postRoute_cap,postRoute_res,postRoute_xcap"

foreach corner $CORNERS {
    set cap_value  [factor_vector $POSTROUTE_EFFORT $cap_factor($corner)]
    set res_value  [factor_vector $POSTROUTE_EFFORT $res_factor($corner)]
    set xcap_value [factor_vector $POSTROUTE_EFFORT $xcap_factor($corner)]

    puts $tcl_fp "update_rc_corner -name [list $corner] \\"
    puts $tcl_fp "    -postRoute_cap  \{$cap_value\} \\"
    puts $tcl_fp "    -postRoute_res  \{$res_value\} \\"
    puts $tcl_fp "    -postRoute_xcap \{$xcap_value\}"
    puts $tcl_fp ""

    puts $csv_fp "$corner,$POSTROUTE_EFFORT,$cap_factor($corner),$res_factor($corner),$xcap_factor($corner)"
}

close $tcl_fp
close $csv_fp

puts ""
puts [format "%-32s %12s %12s %12s" "CORNER" "CAP" "RES" "XCAP"]
puts [string repeat "-" 72]
foreach corner $CORNERS {
    puts [format "%-32s %12s %12s %12s" \
        $corner $cap_factor($corner) $res_factor($corner) $xcap_factor($corner)]
}

puts ""
puts "Created: $OUTPUT_TCL"
puts "Created: $OUTPUT_CSV"
puts "Review the factors before sourcing the update_rc_corner file in Innovus."
