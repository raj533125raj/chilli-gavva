"""Strict On/Off controls for registered runtime cubes; never changes global pause or approvals."""
import cubes


def apply(record, db_path=None):
    target = record['target'].removeprefix('cube:')
    cube = cubes.get_cube(target,db_path)
    if not cube:
        return 'rejected','unknown cube',None
    action,value = record['action'],record['value']
    if action in ('cube_on','cube_off'):
        if value is not None:
            raise ValueError('cube toggle requires null')
        enabled = action == 'cube_on'
    elif type(value) is bool:
        enabled = value
    elif action == 'set_cube' and isinstance(value,dict) and set(value)=={'enabled'} and type(value['enabled']) is bool:
        enabled = value['enabled']
    elif action == 'set_cube' and isinstance(value,str) and value.lower() in ('on','off'):
        enabled = value.lower()=='on'
    else:
        raise ValueError('invalid cube toggle')
    if enabled and cube.get('planned'):
        return 'held','cube has no implemented runtime',None
    cubes.set_cube_status(target,'on' if enabled else 'off',db_path)
    return 'applied','cube enabled' if enabled else 'cube disabled',None
