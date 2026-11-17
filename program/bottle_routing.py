"""Logical sample identities are independent from physical valve ports."""

def sample_port_map(config):
    if 'sample_port_map' not in config:
        return {str(i): i for i in range(1, 7)}
    mapping = config['sample_port_map']
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError('sample_port_map 须填写逻辑瓶号到物理阀口的映射')
    if any(type(k) is not str or k not in [str(i) for i in range(1,7)] or
           type(v) is not int or v not in range(1,8) for k,v in mapping.items()):
        raise ValueError('瓶号须为1—6字符串，物理样品阀口须为1—7整数，不能占用来源/空气口')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('样品瓶映射不能重复使用物理阀口')
    return dict(mapping)

def sample_port(config, sample):
    if type(sample) is not int or str(sample) not in sample_port_map(config):
        raise ValueError(f'{sample}号瓶未配置物理阀口，请核实接管映射')
    return sample_port_map(config)[str(sample)]

def logical_sample(config, port):
    return next((int(k) for k,v in sample_port_map(config).items() if v == port), None)
