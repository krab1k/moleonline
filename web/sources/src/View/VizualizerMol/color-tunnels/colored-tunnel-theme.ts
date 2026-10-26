/**
 * Copyright (c) 2018 mol* contributors, licensed under MIT, See LICENSE file for more info.
 *
 * @author Alexander Rose <alexander.rose@weirdbyte.de>
 */

import type { ColorTheme } from 'molstar/lib/mol-theme/color';
import { Color } from 'molstar/lib/mol-util/color';
import { Location } from 'molstar/lib/mol-model/location';
import { ShapeGroup } from 'molstar/lib/mol-model/shape';
import { ParamDefinition, ParamDefinition as PD } from 'molstar/lib/mol-util/param-definition';
import { ThemeDataContext } from 'molstar/lib/mol-theme/theme';
import { isPositionLocation } from 'molstar/lib/mol-geo/util/location-iterator';
import { ColorNames } from 'molstar/lib/mol-util/color/names';
import { SizeTheme } from 'molstar/lib/mol-theme/size';
import { Profile } from 'molstar/lib/extensions/sb-ncbr/tunnels/data-model';
import { LayersInfo } from '../../../DataInterface';
import { gradientColors } from './property-color';
import { centreLineOf, closestPoint } from './centre-line';

const DefaultColor = Color(0xCCCCCC);
const DescriptionColor = 'Assigns colors as defined by the shape object.';

export const ShapeGroupColorThemeParams = {
    centreLine: ParamDefinition.Value<Profile[]>([], { isHidden: true }),
    layers: ParamDefinition.Value<LayersInfo[]>([], { isHidden: true }),
};
export type ShapeGroupColorThemeParams = typeof ShapeGroupColorThemeParams

// Colours each vertex as the closest centreline point. The colours are read from the gradient when
// the theme is made, so a theme made again from the same props takes the gradient drawn since.
export function ShapeGroupColorTheme(ctx: ThemeDataContext, props: PD.Values<ShapeGroupColorThemeParams>): ColorTheme<ShapeGroupColorThemeParams> {
    const line = centreLineOf(props.centreLine);
    const colors = gradientColors(props.centreLine.map(p => p.Distance), props.layers);
    return {
        factory: ShapeGroupColorTheme,
        granularity: 'vertex',
        color: (location: Location): Color => {
            if (!isPositionLocation(location)) return ColorNames.black;
            if (colors.length === 0) return DefaultColor;
            const [x, y, z] = location.position;
            return colors[closestPoint(line, x, y, z)];
        },
        props,
        description: DescriptionColor
    };
}


const DefaultSize = 1;
const Description = 'Assigns sizes as defined by the shape object.';

export const ShapeGroupSizeThemeParams = {};
export type ShapeGroupSizeThemeParams = typeof ShapeGroupSizeThemeParams

export function ShapeGroupSizeTheme(ctx: ThemeDataContext, props: PD.Values<ShapeGroupSizeThemeParams>): SizeTheme<ShapeGroupSizeThemeParams> {
    return {
        factory: ShapeGroupSizeTheme,
        granularity: 'groupInstance',
        size: (location: Location): number => {
            if (ShapeGroup.isLocation(location)) {
                return location.shape.getSize(location.group, location.instance);
            }
            return DefaultSize;
        },
        props,
        description: Description
    };
}
