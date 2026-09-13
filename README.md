# check_psw_chain.tcl
#
# Read-only Innovus audit for power-switch enable chains.
# The script does not create, delete, reconnect, place, or route any object.
#
# Typical usage:
#   source check_psw_chain.tcl
#   pswChainCheck::run \
#       -master_patterns {<EXACT_PSW_MASTER_OR_PATTERN>} \
#       -in_pin E \
#       -out_pin O \
#       -root_ports {fgotn_power_switch_tofunc} \
#       -end_ports  {fgotn_power_switch_en_out} \
#       -report psw_chain_check.rpt \
#       -csv psw_chain_instances.csv
#
# To identify the PSW master first, examples:
#   dbGet -u top.insts.cell.name *PSW*
#   dbGet -u top.insts.cell.name *SWITCH*
#
# Use an exact master name whenever possible. Broad patterns can accidentally
# include non-PSW cells that also have E/O pins.

namespace eval pswChainCheck {
    variable VERSION "1.0"
}

proc pswChainCheck::_dbget {args} {
    set command [linsert $args 0 dbGet]
    if {[catch {uplevel #0 $command} value]} {
        return ""
    }
    if {$value eq "0x0" || $value eq "NULL"} {
        return ""
    }
    return $value
}

proc pswChainCheck::_unique {items} {
    set result {}
    array set seen {}
    foreach item $items {
        if {$item eq ""} { continue }
        if {![info exists seen($item)]} {
            set seen($item) 1
            lappend result $item
        }
    }
    return $result
}

proc pswChainCheck::_pin_base_name {term_name} {
    if {$term_name eq ""} { return "" }
    return [lindex [split $term_name "/"] end]
}

proc pswChainCheck::_pin_net {inst_ptr pin_name} {
    foreach term_ptr [_dbget ${inst_ptr}.instTerms] {
        set term_name [_dbget ${term_ptr}.name]
        if {$term_name eq $pin_name || [_pin_base_name $term_name] eq $pin_name} {
            set net_name [_dbget ${term_ptr}.net.name]
            if {$net_name eq ""} { return "<UNCONNECTED>" }
            return $net_name
        }
    }
    return "<MISSING_PIN>"
}

proc pswChainCheck::_port_net {port_name} {
    set term_ptrs [_dbget -p top.terms.name $port_name]
    if {[llength $term_ptrs] == 0} { return "<MISSING_PORT>" }
    set net_name [_dbget [lindex $term_ptrs 0].net.name]
    if {$net_name eq ""} { return "<UNCONNECTED>" }
    return $net_name
}

proc pswChainCheck::_inst_location {inst_ptr} {
    set pt [_dbget ${inst_ptr}.pt]
    if {[llength $pt] >= 2} {
        return [list [lindex $pt 0] [lindex $pt 1]]
    }
    set x [_dbget ${inst_ptr}.box_llx]
    set y [_dbget ${inst_ptr}.box_lly]
    if {$x eq ""} { set x "NA" }
    if {$y eq ""} { set y "NA" }
    return [list $x $y]
}

proc pswChainCheck::_csv {value} {
    set escaped [string map [list "\"" "\"\""] $value]
    return "\"${escaped}\""
}

proc pswChainCheck::_usage {} {
    return {
pswChainCheck::run options

Required:
  -master_patterns LIST  Exact PSW master names or dbGet wildcard patterns.

Optional:
  -inst_patterns LIST    Restrict/check instances by hierarchical name pattern.
  -in_pin NAME           PSW enable input pin. Default: E
  -out_pin NAME          PSW enable output pin. Default: O
  -root_ports LIST       Expected top input ports.
  -end_ports LIST        Expected top output/ack ports.
  -report FILE           Text report. Default: psw_chain_check.rpt
  -csv FILE              Per-instance CSV. Default: psw_chain_instances.csv

Example:
  pswChainCheck::run \
    -master_patterns {PSW_MASTER} \
    -in_pin E -out_pin O \
    -root_ports {fgotn_power_switch_tofunc} \
    -end_ports {fgotn_power_switch_en_out}
}
}

proc pswChainCheck::run {args} {
    variable VERSION

    array set opt {
        -master_patterns {}
        -inst_patterns   {}
        -in_pin          E
        -out_pin         O
        -root_ports      {}
        -end_ports       {}
        -report          psw_chain_check.rpt
        -csv             psw_chain_instances.csv
    }

    if {[llength $args] == 1 && [lindex $args 0] in {-help --help -h}} {
        puts [_usage]
        return
    }
    if {[expr {[llength $args] % 2}] != 0} {
        error "Options must be specified as name/value pairs.\n[_usage]"
    }
    foreach {key value} $args {
        if {![info exists opt($key)]} {
            error "Unknown option: $key\n[_usage]"
        }
        set opt($key) $value
    }
    if {[llength [info commands dbGet]] == 0} {
        error "dbGet is unavailable. Run this script inside Innovus."
    }
    if {[llength $opt(-master_patterns)] == 0} {
        error "-master_patterns is required. Use an exact PSW master name when possible.\n[_usage]"
    }

    # Resolve PSW instances from master patterns.
    set inst_ptrs {}
    foreach master_pattern $opt(-master_patterns) {
        foreach ptr [_dbget -p2 top.insts.cell.name $master_pattern] {
            lappend inst_ptrs $ptr
        }
    }

    # Optional instance-name restriction.
    if {[llength $opt(-inst_patterns)] > 0} {
        set allowed_ptrs {}
        foreach inst_pattern $opt(-inst_patterns) {
            foreach ptr [_dbget -p top.insts.name $inst_pattern] {
                lappend allowed_ptrs $ptr
            }
        }
        array set allowed {}
        foreach ptr [_unique $allowed_ptrs] { set allowed($ptr) 1 }
        set filtered {}
        foreach ptr $inst_ptrs {
            if {[info exists allowed($ptr)]} { lappend filtered $ptr }
        }
        set inst_ptrs $filtered
    }
    set inst_ptrs [_unique $inst_ptrs]

    if {[llength $inst_ptrs] == 0} {
        error "No instances matched master patterns: $opt(-master_patterns)"
    }

    array set inst_ptr_by_name {}
    array set master {}
    array set in_net {}
    array set out_net {}
    array set loc_x {}
    array set loc_y {}
    array set input_users {}
    array set output_drivers {}

    set inst_names {}
    foreach inst_ptr $inst_ptrs {
        set inst_name [_dbget ${inst_ptr}.name]
        if {$inst_name eq ""} { continue }
        lappend inst_names $inst_name
        set inst_ptr_by_name($inst_name) $inst_ptr
        set master($inst_name) [_dbget ${inst_ptr}.cell.name]
        set in_net($inst_name) [_pin_net $inst_ptr $opt(-in_pin)]
        set out_net($inst_name) [_pin_net $inst_ptr $opt(-out_pin)]
        lassign [_inst_location $inst_ptr] loc_x($inst_name) loc_y($inst_name)

        if {$in_net($inst_name) ni {"<UNCONNECTED>" "<MISSING_PIN>"}} {
            lappend input_users($in_net($inst_name)) $inst_name
        }
        if {$out_net($inst_name) ni {"<UNCONNECTED>" "<MISSING_PIN>"}} {
            lappend output_drivers($out_net($inst_name)) $inst_name
        }
    }
    set inst_names [lsort -dictionary [_unique $inst_names]]

    array set pred {}
    array set succ {}
    foreach inst_name $inst_names {
        set pred($inst_name) {}
        set succ($inst_name) {}
        if {[info exists output_drivers($in_net($inst_name))]} {
            foreach candidate $output_drivers($in_net($inst_name)) {
                if {$candidate ne $inst_name} { lappend pred($inst_name) $candidate }
            }
        }
        if {[info exists input_users($out_net($inst_name))]} {
            foreach candidate $input_users($out_net($inst_name)) {
                if {$candidate ne $inst_name} { lappend succ($inst_name) $candidate }
            }
        }
        set pred($inst_name) [_unique $pred($inst_name)]
        set succ($inst_name) [_unique $succ($inst_name)]
    }

    # Resolve expected root/end port nets.
    array set root_net_to_ports {}
    array set end_net_to_ports {}
    set root_port_rows {}
    set end_port_rows {}
    foreach port $opt(-root_ports) {
        set net [_port_net $port]
        lappend root_port_rows [list $port $net]
        if {$net ni {"<UNCONNECTED>" "<MISSING_PORT>"}} {
            lappend root_net_to_ports($net) $port
        }
    }
    foreach port $opt(-end_ports) {
        set net [_port_net $port]
        lappend end_port_rows [list $port $net]
        if {$net ni {"<UNCONNECTED>" "<MISSING_PORT>"}} {
            lappend end_net_to_ports($net) $port
        }
    }

    set natural_roots {}
    set expected_roots {}
    set orphan_roots {}
    set natural_ends {}
    set expected_ends {}
    set open_ends {}
    set branches {}
    set merges {}
    set missing_inputs {}
    set missing_outputs {}

    foreach inst_name $inst_names {
        if {$in_net($inst_name) in {"<UNCONNECTED>" "<MISSING_PIN>"}} {
            lappend missing_inputs $inst_name
        }
        if {$out_net($inst_name) in {"<UNCONNECTED>" "<MISSING_PIN>"}} {
            lappend missing_outputs $inst_name
        }
        if {[llength $pred($inst_name)] == 0} {
            lappend natural_roots $inst_name
            if {[info exists root_net_to_ports($in_net($inst_name))]} {
                lappend expected_roots $inst_name
            } else {
                lappend orphan_roots $inst_name
            }
        }
        if {[llength $succ($inst_name)] == 0} {
            lappend natural_ends $inst_name
            if {[info exists end_net_to_ports($out_net($inst_name))]} {
                lappend expected_ends $inst_name
            } else {
                lappend open_ends $inst_name
            }
        }
        if {[llength $succ($inst_name)] > 1} { lappend branches $inst_name }
        if {[llength $pred($inst_name)] > 1} { lappend merges $inst_name }
    }

    # Divide the graph into unbranched segments. A legal serial chain normally
    # appears as one segment from a root to an end port.
    array set visited {}
    array set segment_id {}
    array set segment_order {}
    set segments {}
    set segment_no 0
    set start_candidates $natural_roots
    foreach inst_name $inst_names {
        if {[llength $pred($inst_name)] != 1} { lappend start_candidates $inst_name }
    }
    set start_candidates [_unique $start_candidates]

    foreach start $start_candidates {
        if {[info exists visited($start)]} { continue }
        incr segment_no
        set sequence {}
        set current $start
        set termination ""
        while {1} {
            if {[info exists visited($current)]} {
                set termination "LOOP_OR_REVISIT:$current"
                break
            }
            set visited($current) 1
            lappend sequence $current
            set segment_id($current) $segment_no
            set segment_order($current) [llength $sequence]

            set scount [llength $succ($current)]
            if {$scount == 0} {
                if {[info exists end_net_to_ports($out_net($current))]} {
                    set termination "END_PORT:[join $end_net_to_ports($out_net($current)) ,]"
                } elseif {$out_net($current) eq "<UNCONNECTED>"} {
                    set termination "UNCONNECTED_OUTPUT"
                } elseif {$out_net($current) eq "<MISSING_PIN>"} {
                    set termination "MISSING_OUTPUT_PIN"
                } else {
                    set termination "NO_PSW_SUCCESSOR"
                }
                break
            }
            if {$scount > 1} {
                set termination "BRANCH:[join $succ($current) ,]"
                break
            }
            set next [lindex $succ($current) 0]
            if {[llength $pred($next)] > 1} {
                set termination "MERGE_BEFORE:$next"
                break
            }
            set current $next
        }
        lappend segments [list $segment_no $sequence $termination]
    }

    # Anything still unvisited belongs to a cycle or to the far side of a
    # branch/merge. Trace it as an additional segment so no PSW is omitted.
    foreach start $inst_names {
        if {[info exists visited($start)]} { continue }
        incr segment_no
        set sequence {}
        set current $start
        set termination ""
        while {1} {
            if {[info exists visited($current)]} {
                set termination "LOOP_OR_REVISIT:$current"
                break
            }
            set visited($current) 1
            lappend sequence $current
            set segment_id($current) $segment_no
            set segment_order($current) [llength $sequence]
            if {[llength $succ($current)] != 1} {
                if {[llength $succ($current)] == 0} {
                    if {[info exists end_net_to_ports($out_net($current))]} {
                        set termination "END_PORT:[join $end_net_to_ports($out_net($current)) ,]"
                    } else {
                        set termination "NO_PSW_SUCCESSOR"
                    }
                } else {
                    set termination "BRANCH:[join $succ($current) ,]"
                }
                break
            }
            set current [lindex $succ($current) 0]
        }
        lappend segments [list $segment_no $sequence $termination]
    }

    set loop_segments {}
    foreach segment $segments {
        if {[string match "LOOP_OR_REVISIT:*" [lindex $segment 2]]} {
            lappend loop_segments [lindex $segment 0]
        }
    }

    # Write human-readable report.
    set report_fh [open $opt(-report) w]
    puts $report_fh "PSW ENABLE CHAIN AUDIT"
    puts $report_fh "Script version       : $VERSION"
    puts $report_fh "Design               : [_dbget top.name]"
    puts $report_fh "PSW master patterns  : $opt(-master_patterns)"
    puts $report_fh "Instance patterns    : $opt(-inst_patterns)"
    puts $report_fh "Enable pins          : $opt(-in_pin) -> $opt(-out_pin)"
    puts $report_fh ""
    puts $report_fh "SUMMARY"
    puts $report_fh "  Total PSW instances          : [llength $inst_names]"
    puts $report_fh "  Unbranched segments          : [llength $segments]"
    puts $report_fh "  Natural chain roots          : [llength $natural_roots]"
    puts $report_fh "  Roots on expected input port : [llength $expected_roots]"
    puts $report_fh "  Roots not on expected port   : [llength $orphan_roots]"
    puts $report_fh "  Natural chain ends           : [llength $natural_ends]"
    puts $report_fh "  Ends on expected output port : [llength $expected_ends]"
    puts $report_fh "  Ends without expected port   : [llength $open_ends]"
    puts $report_fh "  Branch points                : [llength $branches]"
    puts $report_fh "  Merge points                 : [llength $merges]"
    puts $report_fh "  Loop/revisit segments        : [llength $loop_segments]"
    puts $report_fh "  Missing/unconnected E pins   : [llength $missing_inputs]"
    puts $report_fh "  Missing/unconnected O pins   : [llength $missing_outputs]"
    puts $report_fh ""

    puts $report_fh "EXPECTED PORT MAPPING"
    foreach row $root_port_rows { puts $report_fh "  ROOT [lindex $row 0] -> [lindex $row 1]" }
    foreach row $end_port_rows  { puts $report_fh "  END  [lindex $row 0] -> [lindex $row 1]" }
    puts $report_fh ""

    puts $report_fh "CHAIN/SEGMENT SUMMARY"
    foreach segment $segments {
        lassign $segment sid sequence termination
        set first [lindex $sequence 0]
        set last [lindex $sequence end]
        puts $report_fh [format "  SEGMENT %-4d depth=%-6d start=%s end=%s" \
            $sid [llength $sequence] $first $last]
        puts $report_fh "    input_net   : $in_net($first)"
        puts $report_fh "    output_net  : $out_net($last)"
        puts $report_fh "    termination : $termination"
    }
    puts $report_fh ""

    puts $report_fh "ANOMALIES"
    puts $report_fh "  ORPHAN_ROOTS       : $orphan_roots"
    puts $report_fh "  OPEN_ENDS          : $open_ends"
    puts $report_fh "  BRANCH_POINTS      : $branches"
    puts $report_fh "  MERGE_POINTS       : $merges"
    puts $report_fh "  LOOP_SEGMENTS      : $loop_segments"
    puts $report_fh "  BAD_INPUT_PINS     : $missing_inputs"
    puts $report_fh "  BAD_OUTPUT_PINS    : $missing_outputs"
    puts $report_fh ""

    puts $report_fh "PER-INSTANCE CONNECTIVITY"
    puts $report_fh "  segment order instance master x y input_net predecessors output_net successors"
    foreach inst_name $inst_names {
        puts $report_fh [format "  %s %s %s %s %s %s %s {%s} %s {%s}" \
            $segment_id($inst_name) $segment_order($inst_name) \
            $inst_name $master($inst_name) $loc_x($inst_name) $loc_y($inst_name) \
            $in_net($inst_name) [join $pred($inst_name) ,] \
            $out_net($inst_name) [join $succ($inst_name) ,]]
    }
    close $report_fh

    # Write machine-friendly CSV for sorting/plotting.
    set csv_fh [open $opt(-csv) w]
    puts $csv_fh "segment,order,instance,master,x,y,input_net,predecessor_count,predecessors,output_net,successor_count,successors"
    foreach inst_name $inst_names {
        set row [list \
            $segment_id($inst_name) \
            $segment_order($inst_name) \
            $inst_name \
            $master($inst_name) \
            $loc_x($inst_name) \
            $loc_y($inst_name) \
            $in_net($inst_name) \
            [llength $pred($inst_name)] \
            [join $pred($inst_name) ";"] \
            $out_net($inst_name) \
            [llength $succ($inst_name)] \
            [join $succ($inst_name) ";"]]
        set quoted {}
        foreach value $row { lappend quoted [_csv $value] }
        puts $csv_fh [join $quoted ,]
    }
    close $csv_fh

    puts "PSW chain audit completed."
    puts "  Report : $opt(-report)"
    puts "  CSV    : $opt(-csv)"
    puts "  PSWs   : [llength $inst_names]"
    puts "  Roots  : [llength $natural_roots]"
    puts "  Ends   : [llength $natural_ends]"
    puts "  Branch : [llength $branches]"
    puts "  Merge  : [llength $merges]"
    puts "  Loops  : [llength $loop_segments]"

    return [list \
        total_psw [llength $inst_names] \
        segments [llength $segments] \
        roots [llength $natural_roots] \
        expected_roots [llength $expected_roots] \
        ends [llength $natural_ends] \
        expected_ends [llength $expected_ends] \
        branches [llength $branches] \
        merges [llength $merges] \
        loops [llength $loop_segments]]
}

puts "Loaded pswChainCheck $pswChainCheck::VERSION. Run 'pswChainCheck::run -help' for usage."
