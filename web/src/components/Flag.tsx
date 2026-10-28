import { FLAG_URIS } from "./flags.generated";
import { Icon } from "./Icon";

/** The flag of a country, by its name as in the universe (country-flag-icons, MIT licence); a globe if it has none. */
export function Flag({ country }: { country: string }) {
  const uri = FLAG_URIS[country];
  return uri ? <img className="flag" src={uri} alt="" width={30} height={20} /> : <Icon name="globe" size={24} />;
}
